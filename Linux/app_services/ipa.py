"""Bounded IPA preflight and executable-mode repair; never extracts an archive."""
import copy
import os
from pathlib import Path, PurePosixPath
import plistlib
import stat
import struct
import zipfile

from .catalog import Target, _version


class InstallError(ValueError):
    pass


MAX_IPA = 2 * 1024**3
MAX_UNPACKED = 8 * 1024**3


def binary_slices(data):
    """Inspect 32-bit ARM Mach-O load commands, including encrypted slices."""
    if len(data) < 28:
        raise InstallError("The app executable is truncated.")
    if data[:4] == b"\xca\xfe\xba\xbe":
        count = struct.unpack_from(">I", data, 4)[0]
        if not 0 < count <= 32 or len(data) < 8 + count * 20:
            raise InstallError("Invalid universal executable.")
        result = []
        for i in range(count):
            _, _, offset, size, _ = struct.unpack_from(">IIIII", data, 8 + i * 20)
            if offset + size > len(data):
                raise InstallError("The app executable is truncated.")
            result.extend(binary_slices(data[offset:offset + size]))
        return result
    if data[:4] != b"\xce\xfa\xed\xfe":
        raise InstallError("This IPA needs an unsupported executable format.")
    _, cpu, subtype, _, commands, size, _ = struct.unpack_from("<7I", data)
    if cpu != 12 or commands > 65536 or 28 + size > len(data):
        raise InstallError("This IPA does not contain a supported ARM executable.")
    cursor = 28
    for _ in range(commands):
        if cursor + 8 > 28 + size:
            raise InstallError("Invalid executable load commands.")
        command, length = struct.unpack_from("<II", data, cursor)
        if length < 8 or cursor + length > 28 + size:
            raise InstallError("Invalid executable load commands.")
        if command == 0x21:
            if length < 20 or struct.unpack_from("<I", data, cursor + 16)[0] != 0:
                raise InstallError("This app is encrypted; select a decrypted IPA.")
        cursor += length
    return [subtype & 0xffffff]


def inspect(path, target=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_IPA:
        raise InstallError("Select a regular IPA smaller than 2 GiB.")
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 100000 or sum(e.file_size for e in entries) > MAX_UNPACKED:
                raise InstallError("The unpacked IPA exceeds the supported size.")
            names = set()
            for entry in entries:
                name = entry.filename
                parts = PurePosixPath(name).parts
                if (name in names or name.startswith("/") or ".." in parts or "\\" in name or
                        entry.flag_bits & 1):
                    raise InstallError("The IPA contains unsafe, duplicate or encrypted ZIP members.")
                names.add(name)
            candidates = [e for e in entries if len(PurePosixPath(e.filename).parts) == 3 and
                          e.filename.startswith("Payload/") and e.filename.endswith(".app/Info.plist")]
            if len(candidates) != 1 or candidates[0].file_size > 1024**2:
                raise InstallError("Select an IPA containing one Payload app and Info.plist.")
            info = plistlib.loads(archive.read(candidates[0]))
            bundle = info.get("CFBundleIdentifier")
            executable = info.get("CFBundleExecutable")
            if (not isinstance(bundle, str) or not bundle or not isinstance(executable, str) or
                    not executable or "/" in executable or "\\" in executable or executable in (".", "..")):
                raise InstallError("The app has invalid bundle or executable metadata.")
            member = candidates[0].filename.rsplit("/", 1)[0] + "/" + executable
            entry = archive.getinfo(member)
            if entry.file_size > 256 * 1024**2 or stat.S_ISLNK(entry.external_attr >> 16):
                raise InstallError("The app executable is unsupported or too large.")
            slices = binary_slices(archive.read(entry))
            if target:
                if target.arch == "armv6" and not any(s in (0, 6) for s in slices):
                    raise InstallError("This app needs a newer processor than the guest.")
                if target.arch == "armv7" and not any(s in (0, 6, 9) for s in slices):
                    raise InstallError("This app needs a newer processor than the guest.")
                minimum = info.get("MinimumOSVersion")
                if minimum and _version(minimum) > _version(target.ios):
                    raise InstallError("This app requires iOS {}; the guest runs {}.".format(minimum, target.ios))
                families = info.get("UIDeviceFamily")
                if families and int(target.family) not in families:
                    raise InstallError("This app is not made for the guest device family.")
            return {"bundle_id": bundle, "name": info.get("CFBundleDisplayName", bundle),
                    "executable": member, "repair": not bool((entry.external_attr >> 16) & 0o111),
                    "minimum_os": info.get("MinimumOSVersion"), "bytes": path.stat().st_size}
    except (OSError, zipfile.BadZipFile, KeyError, plistlib.InvalidFileException, struct.error, TypeError) as error:
        raise InstallError("The IPA is incomplete or has invalid app metadata: " + str(error)) from error


def prepare(source, destination, target, cancelled=lambda: False):
    """Always snapshot into private scratch; repair only the main binary mode."""
    metadata = inspect(source, target)
    destination = Path(destination)
    try:
        with zipfile.ZipFile(source) as archive, destination.open("xb") as raw:
            os.chmod(destination, 0o600)
            with zipfile.ZipFile(raw, "w", allowZip64=True) as output:
                for entry in archive.infolist():
                    fixed = copy.copy(entry)
                    if entry.filename == metadata["executable"] and metadata["repair"]:
                        fixed.create_system = 3
                        fixed.external_attr = ((stat.S_IFREG | 0o755) << 16) | (entry.external_attr & 0xffff)
                    with archive.open(entry) as reader, output.open(fixed, "w") as writer:
                        while True:
                            if cancelled():
                                raise InstallError("Install preparation cancelled.")
                            chunk = reader.read(65536)
                            if not chunk:
                                break
                            writer.write(chunk)
            raw.flush()
            os.fsync(raw.fileno())
        checked = inspect(destination, target)
        if checked["bundle_id"] != metadata["bundle_id"] or checked["executable"] != metadata["executable"]:
            raise InstallError("The source IPA changed during preparation.")
        return checked
    except BaseException:
        destination.unlink(missing_ok=True)
        raise

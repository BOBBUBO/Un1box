"""Read-only proposal for explicitly adopting the earlier Windows preview library.

This converts configuration metadata only. It never copies writable state,
changes the preview library, takes a lease or claims native asset admission.
The future Windows worker must acquire the SAME running.lock used by WinForms.
"""
import hashlib
import json
import ntpath
from pathlib import Path, PureWindowsPath
import re

from .library import RecordError, check_collection

MAX_LIBRARY_BYTES = 1024 * 1024


class PreviewImportError(ValueError):
    pass


def windows_path(value):
    """Normalize syntax, preserving spelling used by the preview state binding."""
    if not isinstance(value, str) or not value or "\0" in value:
        raise PreviewImportError("The preview library contains an invalid path.")
    drive, tail = ntpath.splitdrive(value)
    if not drive or not tail.startswith(("\\", "/")):
        raise PreviewImportError("Preview file paths must be absolute Windows paths.")
    # Extended device namespaces have additional alias rules which this metadata
    # importer cannot establish safely. Normal drive/UNC paths remain supported.
    if value.startswith(("\\\\?\\", "\\\\.\\")):
        raise PreviewImportError("Device namespace paths require native Windows admission.")
    return PureWindowsPath(ntpath.normpath(value))


def windows_absolute(value):
    """Case-insensitive comparison only; never persist this spelling."""
    return PureWindowsPath(ntpath.normcase(str(windows_path(value))))


def proposal(data, preview_root, source_name="library.json"):
    """Return immutable-bound metadata for a later, explicit import decision.

    PureWindowsPath makes these checks testable on Linux; it is never evidence
    that a Windows directory exists, is private, or is currently unlocked.
    """
    if not isinstance(data, bytes) or len(data) > MAX_LIBRARY_BYTES:
        raise PreviewImportError("Preview library exceeds the supported 1 MiB size.")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PreviewImportError("Duplicate preview library field: " + key)
            result[key] = value
        return result

    try:
        records = json.loads(data.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PreviewImportError("The preview library must contain UTF-8 JSON.") from error
    if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        raise PreviewImportError("Choose the original Windows preview library.json.")
    root = windows_path(preview_root)
    expected = {"Id", "Name", "BasePath", "RomPath", "GuestPackagePath"}
    devices = []
    for record in records:
        if set(record) != expected:
            raise PreviewImportError("Unsupported Windows preview record; files were retained.")
        identity = record["Id"]
        if not isinstance(identity, str) or not re.fullmatch("[a-fA-F0-9]{32}", identity):
            raise PreviewImportError("Invalid Windows preview device identity.")
        name = record["Name"]
        if not isinstance(name, str) or not name.strip():
            raise PreviewImportError("The preview device needs a name.")
        base = windows_path(record["BasePath"])
        package = windows_path(record["GuestPackagePath"])
        if not isinstance(record["RomPath"], str):
            raise PreviewImportError("The preview boot file path must be a string.")
        rom = windows_path(record["RomPath"]) if record["RomPath"] else ""
        # Preserve the old state location exactly. Adopting metadata must never
        # create a second overlay or silently reinterpret another host's state.
        state = root / "devices" / identity
        devices.append({"id": identity.lower(), "name": name, "base": str(base),
                        "state": str(state), "guest_package": str(package),
                        "bootrom": str(rom), "audio": True})
    try:
        check_collection(devices, "", windows_absolute)
    except RecordError as error:
        raise PreviewImportError(str(error)) from error
    return {"source": source_name, "source_sha256": hashlib.sha256(data).hexdigest(),
            "preview_root": str(root), "devices": devices,
            "requires_explicit_confirmation": True,
            "native_admission": "not performed", "writable_state_copied": False,
            "lease_name": "running.lock"}


def read_proposal(library, preview_root):
    """Read the chosen original library without writing to either library."""
    path = Path(library)
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)() or not path.is_file():
        raise PreviewImportError("Choose a regular Windows preview library.json.")
    with path.open("rb") as stream:
        data = stream.read(MAX_LIBRARY_BYTES + 1)
    return proposal(data, preview_root, str(path))

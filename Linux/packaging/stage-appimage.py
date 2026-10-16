#!/usr/bin/env python3
"""Stage an audited AppDir from an explicit hash-pinned runtime manifest; no downloads."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
HOST_LIBRARIES = {"libc.so.6", "libm.so.6", "libdl.so.2", "libpthread.so.0", "librt.so.1", "libresolv.so.2", "libutil.so.1", "ld-linux-x86-64.so.2", "libEGL.so.1", "libGLESv2.so.2", "libGL.so.1", "libGLX.so.0", "libOpenGL.so.0", "libGLdispatch.so.0", "libvulkan.so.1", "libstdc++.so.6", "libgcc_s.so.1"}
# These must match the host Mesa/vendor driver stack as well. Old bundled
# versions can hide entry points required by a newer host EGL provider.
HOST_LIBRARIES.update({"libgbm.so.1", "libdrm.so.2"})
# Host EGL providers link Wayland even when our context is surfaceless. An old
# bundled client can prevent a newer Mesa vendor from loading at all (for
# example, wl_proxy_marshal_flags was added after Debian 11's Wayland 1.18).
HOST_LIBRARIES.update({"libwayland-client.so.0", "libwayland-server.so.0",
                       "libwayland-cursor.so.0", "libwayland-egl.so.1"})
FORBIDDEN = {"device.lock.json", "nor.bin", "kboot.bin", "gid-blobs.bin", "iboot.bin", "bootrom.bin", ".dirty", ".base-identity"}
REQUIRED = {"usr/bin/python3", "usr/bin/lighttouch-linux", "usr/lib/lighttouch/libqemu-arm.so", "usr/share/licenses/LightTouch/SOURCE.txt"}


def stage_desktop_sources(output):
    launcher = output / "usr/share/lighttouch/launcher"
    launcher.mkdir(parents=True, exist_ok=True)
    for name in ("main.py", "model.py", "session.py", "preparation.py", "host.py", "experimental_cache.py"):
        shutil.copyfile(ROOT.parent / "launcher" / name, launcher / name)
    shared = output / "usr/share/lighttouch/lighttouch_desktop"
    shared.mkdir(parents=True, exist_ok=True)
    for source in sorted((ROOT.parents[1] / "Desktop/lighttouch_desktop").glob("*.py")):
        shutil.copyfile(source, shared / source.name)
    services = output / "usr/share/lighttouch/app_services"
    services.mkdir(parents=True, exist_ok=True)
    for source in sorted((ROOT.parent / "app_services").glob("*.py")):
        shutil.copyfile(source, services / source.name)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def target(value):
    if not isinstance(value, str) or "\\" in value or "\0" in value:
        raise ValueError("Invalid package destination")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value or not value.startswith("usr/"):
        raise ValueError("Package destinations must be normalized paths below usr/")
    lower = value.lower()
    if (any(piece.lower() in FORBIDDEN or piece.lower() in ("nand", "private-assets", "fixtures") for piece in path.parts)
            or lower.endswith((".ipsw", ".itbase", ".itpack", ".ipa", ".dmg"))):
        raise ValueError("Private guest assets must remain outside the AppImage: " + value)
    if path.name in HOST_LIBRARIES:
        raise ValueError("Do not bundle host glibc or graphics provider libraries: " + value)
    return path


def read_manifest(path):
    value = json.loads(Path(path).read_text())
    if value.get("schema") != 1 or value.get("architecture") != "x86_64" or value.get("glibc_max") != "2.31":
        raise ValueError("Expected schema1 x86_64/glibc2.31 runtime manifest")
    python = value.get("python_stdlib")
    if not isinstance(python, str) or not re.fullmatch(r"python3\.(9|1[0-9])", python):
        raise ValueError("Declare bundled Python3.9+ standard library directory")
    files = value.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("Explicit runtime file manifest required")
    seen = set()
    total = 0
    for entry in files:
        name = str(target(entry["destination"]))
        if name in seen:
            raise ValueError("Duplicate package destination: " + name)
        seen.add(name)
        source = Path(entry["source"])
        if not source.is_absolute() or source.is_symlink() or not source.is_file():
            raise ValueError("Runtime sources must be absolute regular files (resolve symlinks before auditing)")
        if not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]) or sha(source) != entry["sha256"]:
            raise ValueError("Runtime source checksum mismatch: " + str(source))
        if not isinstance(entry.get("license"), str) or not entry["license"].strip():
            raise ValueError("Every bundled file needs license provenance")
        total += source.stat().st_size
    required = REQUIRED | {"usr/lib/" + python + "/os.py", "usr/lib/" + python + "/tkinter/__init__.py", "usr/share/tcltk/tcl8.6/init.tcl", "usr/share/tcltk/tk8.6/tk.tcl"}
    if not required <= seen or not any(n.startswith("usr/lib/" + python + "/lib-dynload/_tkinter.") and n.endswith(".so") for n in seen):
        raise ValueError("Manifest must include native frontend/engine, Python/Tk/Tcl, and corresponding-source notice")
    if total > 4 * 1024**3:
        raise ValueError("Runtime payload exceeds 4GiB; inspect for unintended private assets")
    return value


def elf_requirements(path):
    with path.open("rb") as stream:
        head = stream.read(20)
    if not head.startswith(b"\x7fELF"):
        return None
    if len(head) != 20 or head[4:6] != b"\x02\x01" or struct.unpack("<H", head[18:20])[0] != 62:
        raise ValueError("AppImage payload must contain only x86_64 Linux ELF files: " + str(path))
    versions = subprocess.run(["readelf", "--version-info", str(path)], capture_output=True, text=True, check=True).stdout
    for name in set(re.findall(r"\bGLIBC_[A-Za-z0-9_.]+\b", versions)):
        version = re.fullmatch(r"GLIBC_(\d+)\.(\d+)(?:\.(\d+))?", name)
        # ABI tags such as GLIBC_ABI_DT_RELR also require newer loaders;
        # they must not bypass the numeric symbol-version floor.
        if version is None:
            raise ValueError("Payload uses unsupported glibc ABI tag %s: %s" % (name, path))
        if tuple(int(part or 0) for part in version.groups()) > (2, 31, 0):
            raise ValueError("Payload needs newer than glibc2.31: " + str(path))
    dynamic = subprocess.run(["readelf", "-d", str(path)], capture_output=True, text=True, check=True).stdout
    for search in re.findall(r"\((?:RPATH|RUNPATH)\).*\[([^\]]*)\]", dynamic):
        for part in search.split(":"):
            if not (part == "$ORIGIN" or part.startswith("$ORIGIN/") or part == "${ORIGIN}" or part.startswith("${ORIGIN}/")):
                raise ValueError("Payload has a host-specific RPATH/RUNPATH: " + str(path))
    return re.findall(r"\(NEEDED\).*\[([^\]]+)\]", dynamic)


def stage(manifest_path, output):
    manifest = read_manifest(manifest_path)
    output = Path(output).absolute()
    if output.exists():
        raise ValueError("AppDir destination already exists; do not overwrite an audited package")
    available = {PurePosixPath(f["destination"]).name for f in manifest["files"] if str(PurePosixPath(f["destination"]).parent) in ("usr/lib", "usr/lib/lighttouch")}
    elf_report = {}
    cxx_versions = set()
    # Validate complete payload before creating any destination.
    for entry in manifest["files"]:
        source = Path(entry["source"])
        needed = elf_requirements(source)
        if needed is not None:
            absent = set(needed) - available - HOST_LIBRARIES
            if absent:
                raise ValueError("Unbundled native dependencies for %s: %s" % (source, ", ".join(sorted(absent))))
            elf_report[entry["destination"]] = needed
            versions = subprocess.run(["readelf", "--version-info", str(source)], capture_output=True, text=True, check=True).stdout
            cxx_versions.update(re.findall(r"\b(?:GLIBCXX|CXXABI)_[A-Za-z0-9_.]+\b", versions))
    output.mkdir(parents=True, mode=0o755)
    for entry in manifest["files"]:
        destination = output / entry["destination"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(entry["source"], destination)
        destination.chmod(0o755 if entry["destination"].startswith("usr/bin/") or entry["destination"].endswith(".so") else 0o644)
        if sha(destination) != entry["sha256"]:
            raise ValueError("Copied payload checksum mismatch")
    stage_desktop_sources(output)
    for name in ("AppRun", "lighttouch.desktop", "lighttouch.svg"):
        shutil.copyfile(ROOT / name, output / name)
    (output / "AppRun").chmod(0o755)
    report = {"schema": 1, "architecture": "x86_64", "compatibility_target": "glibc2.31; actual distro qualification pending", "manifest_sha256": sha(manifest_path), "host_provided_sonames": sorted(HOST_LIBRARIES), "ELF_NEEDED": elf_report, "host_cpp_symbol_versions": sorted(cxx_versions),
              "files": {str(p.relative_to(output)): sha(p) for p in sorted(output.rglob("*")) if p.is_file()}, "guest_assets_included": False, "guest_acceptance": False}
    (output / "runtime-manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = stage(args.manifest, args.output)
    print(json.dumps({"appdir": str(args.output), "file_count": len(report["files"]), "guest_acceptance": False}))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))

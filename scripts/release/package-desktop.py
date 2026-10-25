#!/usr/bin/env python3
"""Assemble the Rust desktop app's folder for Linux or Windows, with every component's notices for its licence page.

    package-desktop.py OUT --os linux|windows --app BIN --firmwarekit BIN --engine LIB --usbmuxd BIN
        --guest-tools DIR [--lib FILE_OR_DIR]... [--notices GROUP=DIR]... [--group-source GROUP=URL]... [--source-reference URL] [--zip]

OUT/                    lighttouch[.exe]; Linux also gets LightTouch, a launcher that puts lib/ on the library path
OUT/resources/          the engine, firmwarekit, the USB bridge, guest-tools/ (and on Windows, every --lib DLL)
OUT/lib/                (Linux) the --lib shared libraries the engine and the USB bridge need
OUT/resources/licenses/ frontend/ and firmware/ (stage-notices.py for the app and firmwarekit on this target),
                        plus each --notices GROUP (engine, usb), copied with a manifest.json in the schema
                        lighttouch_model::notices reads; its source link is --group-source, else
                        --source-reference (LightTouch's)

The binaries are built beforehand: the app and firmwarekit with `cargo build --release` (packaged builds use
--features lighttouch-services/vendored-openssl, so the app needs no system OpenSSL), the engine with qemu-ios's
contrib/portable scripts, the USB bridge with its own build. --zip also writes OUT.zip.
"""
import argparse, hashlib, json, re, shutil, subprocess, sys, zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NAME = re.compile(r"[A-Za-z0-9._-]+")  # what the licence page accepts in a notice path
TRIPLES = {"linux": "x86_64-unknown-linux-gnu", "windows": "x86_64-pc-windows-gnu"}
LAUNCHER = """#!/bin/sh
# Puts the bundled libraries (libfdt, libimobiledevice) after the system's on the library path.
HERE=$(dirname "$(readlink -f "$0")")
export LD_LIBRARY_PATH="$HERE/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$HERE/lighttouch" "$@"
"""


def copy(src, dst, mode=None):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    if mode is not None:
        dst.chmod(mode)


def notices(group_dir, source, source_reference):
    """Copy a notice folder in, naming files the licence page accepts, and write its manifest."""
    if group_dir.exists():
        shutil.rmtree(group_dir)
    files = []
    for f in sorted(p for p in source.rglob("*") if p.is_file() and p.name != "manifest.json"):
        rel = "/".join(re.sub(r"[^A-Za-z0-9._-]", "_", part) for part in f.relative_to(source).parts)
        copy(f, group_dir / rel)
        files.append({"path": rel, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()})
    if not files:
        raise SystemExit(f"package-desktop: no notices in {source}")
    manifest = {"schema": 1, "source_reference": source_reference, "files": files}
    (group_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("out", type=Path)
    a.add_argument("--os", required=True, choices=sorted(TRIPLES))
    a.add_argument("--app", type=Path, required=True)
    a.add_argument("--firmwarekit", type=Path, required=True)
    a.add_argument("--engine", type=Path, required=True)
    a.add_argument("--usbmuxd", type=Path, required=True)
    a.add_argument("--guest-tools", type=Path, required=True)
    a.add_argument("--lib", type=Path, action="append", default=[], help="a library, or a folder of them")
    a.add_argument("--notices", action="append", default=[], metavar="GROUP=DIR")
    a.add_argument("--group-source", action="append", default=[], metavar="GROUP=URL")
    a.add_argument("--source-reference", default="https://github.com/blake-lucas/LightTouch")
    a.add_argument("--zip", action="store_true")
    args = a.parse_args()
    windows = args.os == "windows"
    exe = ".exe" if windows else ""

    if args.out.exists():
        shutil.rmtree(args.out)
    res = args.out / "resources"
    copy(args.app, args.out / f"lighttouch{exe}", 0o755)
    copy(args.firmwarekit, res / f"firmwarekit{exe}", 0o755)
    copy(args.engine, res / ("libqemu-arm.dll" if windows else "libqemu-arm.so"))
    copy(args.usbmuxd, res / ("lighttouch-usbmuxd.exe" if windows else "usbmuxd-qemu"), 0o755)
    shutil.copytree(args.guest_tools, res / "guest-tools")
    libs = res if windows else args.out / "lib"
    for lib in args.lib:
        for f in sorted(lib.iterdir()) if lib.is_dir() else [lib]:
            if f.is_file():
                copy(f.resolve(), libs / f.name)
    if not windows:
        (args.out / "LightTouch").write_text(LAUNCHER)
        (args.out / "LightTouch").chmod(0o755)

    licenses = res / "licenses"
    stage = REPO / "crates/lighttouch-firmware/stage-notices.py"
    for group, package, features in [("frontend", "lighttouch", "lighttouch-services/vendored-openssl"),
                                     ("firmware", "lighttouch-firmware", "")]:
        command = [sys.executable, str(stage), str(licenses / group), "--package", package,
                   "--target", TRIPLES[args.os], "--source-reference", args.source_reference]
        subprocess.run(command + (["--features", features] if features else []), check=True)
    sources = dict(spec.split("=", 1) for spec in args.group_source)
    for spec in args.notices:
        group, _, folder = spec.partition("=")
        if not NAME.fullmatch(group) or not folder:
            raise SystemExit(f"package-desktop: --notices wants GROUP=DIR, not {spec}")
        notices(licenses / group, Path(folder), sources.get(group, args.source_reference))

    if args.zip:
        archive = args.out.parent / (args.out.name + ".zip")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(args.out.rglob("*")):
                if f.is_file():
                    info = zipfile.ZipInfo.from_file(f, Path(args.out.name) / f.relative_to(args.out))
                    with f.open("rb") as s:
                        z.writestr(info, s.read(), zipfile.ZIP_DEFLATED)
        print(archive)
    print(args.out)


if __name__ == "__main__":
    main()

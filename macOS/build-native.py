#!/usr/bin/env python3
"""Compile the existing Cocoa/Swift helpers with CLT and assemble a test app.

The explicit vendor directory supplies already built ancillary dependencies;
this is not a fully source-rebuilt or notarized release. No firmware is copied.
Run under an external memory/time supervisor when building on a shared host.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor-contents", type=Path, required=True,
                        help="Contents of an existing, trusted native Light Touch.app")
    parser.add_argument("--engine", type=Path, required=True,
                        help="newly compiled native libqemu-arm.dylib")
    parser.add_argument("--output", type=Path, required=True,
                        help="absent destination ending in .app")
    parser.add_argument("--scratch", type=Path, required=True,
                        help="private SwiftPM build directory, outside the source checkout")
    parser.add_argument("--headers", type=Path, default=Path("/opt/homebrew/include"))
    parser.add_argument("--jobs", type=int, choices=[1, 2], default=2)
    parser.add_argument("--source-revision", help="recorded revision for an exported source archive")
    parser.add_argument("--engine-revision", help="recorded QEMU source revision for this native engine")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("this recipe requires macOS, Swift6.2+ and its native SDK")
    source = Path(__file__).resolve().parents[1]
    vendor, engine = args.vendor_contents.resolve(), args.engine.resolve()
    output, scratch = args.output.absolute(), args.scratch.absolute()
    if output.exists() or output.is_symlink() or output.suffix != ".app":
        parser.error("--output must be an absent .app path; existing apps are never replaced")
    if source == scratch or source in scratch.parents:
        parser.error("--scratch must be outside the source checkout")
    for path in [engine, args.headers / "libimobiledevice/lockdown.h"]:
        if not path.exists():
            parser.error("required native dependency absent: " + str(path))
    env = dict(os.environ, LIGHTTOUCH_MAC_VENDOR_CONTENTS=str(vendor),
               LIGHTTOUCH_MAC_C_HEADERS=str(args.headers.resolve()))
    command = ["swift", "build", "--package-path", str(source), "--scratch-path", str(scratch),
               "--configuration", "debug", "--jobs", str(args.jobs)]
    subprocess.run(command, env=env, check=True)
    bin_path = Path(subprocess.check_output(command + ["--show-bin-path"], env=env, text=True).strip())
    contents = output / "Contents"
    for name in ["MacOS", "Frameworks", "Resources"]:
        (contents / name).mkdir(parents=True, exist_ok=True)
    receipt = {"schema": 1, "dependency_origins": {}, "kind": "CLT native development build; no notarization or guest acceptance",
               "compile_argv": command, "source_revision": args.source_revision,
               "engine_revision": args.engine_revision, "rebuilt": {}, "reused": {},
               "excluded": ["vendor Resources/Device (firmware)", "prepared bases and writable device state"]}
    try:
        detected_revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source, text=True, stderr=subprocess.DEVNULL).strip()
        if args.source_revision and args.source_revision != detected_revision:
            raise RuntimeError("source revision does not match checkout")
        receipt["source_revision"] = detected_revision
    except (subprocess.CalledProcessError, FileNotFoundError):
        receipt["source_revision"] = args.source_revision or "unrecorded source archive"

    def copy(src, dst, category):
        receipt["dependency_origins"][str(dst.relative_to(contents))] = str(src)
        if src.is_dir():
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst)
        for file in ([dst] if dst.is_file() else dst.rglob("*")):
            if file.is_file() and not file.is_symlink():
                receipt[category][str(file.relative_to(contents))] = digest(file)

    for name in ["LightTouch", "LightTouchDevice", "LightTouchServices"]:
        copy(bin_path / name, contents / "MacOS" / name, "rebuilt")
    copy(engine, contents / "Frameworks/libqemu-arm.dylib", "rebuilt")
    sparkle = list(scratch.glob("artifacts/sparkle/**/Sparkle.xcframework"))
    if len(sparkle) != 1:
        raise RuntimeError("expected one resolved Sparkle2.10.0 XCFramework: " + str(sparkle))
    sparkle_info = plistlib.loads((sparkle[0] / "Info.plist").read_bytes())
    slices = [item for item in sparkle_info["AvailableLibraries"] if item["SupportedPlatform"] == "macos"]
    if len(slices) != 1:
        raise RuntimeError("expected one universal macOS Sparkle slice")
    sparkle_framework = sparkle[0] / slices[0]["LibraryIdentifier"] / slices[0]["LibraryPath"]
    copy(sparkle_framework, contents / "Frameworks/Sparkle.framework", "reused")
    for name in ["libavcodec.63.1.101.dylib", "libavutil.61.1.101.dylib",
                 "libimobiledevice-1.0.dylib", "libplist-2.0.dylib"]:
        copy(vendor / "Frameworks" / name, contents / "Frameworks" / name, "reused")
    for name in ["firmwarekit", "ipod-helper", "usbmuxd", "iBoot32Patcher", "inetcat"]:
        copy(vendor / "MacOS" / name, contents / "MacOS" / name, "reused")
    # Named allowlist avoids silently bundling firmware from the installed app.
    for name in ["Assets.car", "Guest", "usbmuxd-conf", "licenses"]:
        item = vendor / "Resources" / name
        if item.exists():
            copy(item, contents / "Resources" / name, "reused")
    for name in ["Help.txt", "Sleeping.caar", "special_trick.mp3"]:
        copy(source / "LightTouchMac" / name, contents / "Resources" / name, "rebuilt")
    for item in (source / "LightTouchMac/Resources").iterdir():
        copy(item, contents / "Resources" / item.name, "rebuilt")
    # The native engine may use Homebrew libraries. Copy their complete dylib
    # closure without relocating or editing any installed source dependency.
    pending = [(engine, contents / "Frameworks/libqemu-arm.dylib")]
    bundled_host_libraries = {}
    while pending:
        original, local = pending.pop()
        dependencies = subprocess.check_output(["otool", "-L", str(original)], text=True).splitlines()[1:]
        for line in dependencies:
            dependency = line.strip().split(" (", 1)[0]
            if not dependency.startswith(("/opt/homebrew/", "/usr/local/")):
                continue
            dependency_path = Path(dependency)
            if dependency_path.resolve() == original.resolve():
                continue  # dylib install ID, not a dependency on another file
            destination = contents / "Frameworks" / dependency_path.name
            if dependency_path.name not in bundled_host_libraries:
                if destination.exists():
                    raise RuntimeError("dependency filename collides with vendor file: " + str(destination))
                copy(dependency_path.resolve(), destination, "reused")
                bundled_host_libraries[dependency_path.name] = dependency_path.resolve()
                subprocess.run(["install_name_tool", "-id", "@rpath/" + dependency_path.name,
                                str(destination)], check=True)
                pending.append((dependency_path.resolve(), destination))
            elif bundled_host_libraries[dependency_path.name] != dependency_path.resolve():
                raise RuntimeError("two host libraries share one install name: " + dependency_path.name)
            subprocess.run(["install_name_tool", "-change", dependency,
                            "@loader_path/" + dependency_path.name, str(local)], check=True)
    info = plistlib.loads((source / "Configuration/LightTouchMac-Info.plist").read_bytes())
    info.update(CFBundleExecutable="LightTouch", CFBundleIdentifier="org.lighttouch.native-test",
                CFBundleName="Light Touch Native Test", CFBundleDisplayName="Light Touch Native Test",
                CFBundleVersion="1", CFBundleShortVersionString="0.1", CFBundlePackageType="APPL",
                LSMinimumSystemVersion="14.4", NSPrincipalClass="LightTouchApplication",
                SUEnableAutomaticChecks=False, SUAllowsAutomaticUpdates=False)
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    rpath = "@executable_path/../Frameworks"
    for name in ["LightTouch", "LightTouchDevice", "LightTouchServices"]:
        subprocess.run(["install_name_tool", "-add_rpath", rpath, str(contents / "MacOS" / name)], check=True)
    # Sign our new executable/helper code after modifying load commands. Preserve
    # supplied vendor signatures; this development bundle has no hardened runtime.
    sign_libraries = [contents / "Frameworks" / name for name in bundled_host_libraries]
    for path in sign_libraries + [contents / "Frameworks/libqemu-arm.dylib", contents / "MacOS/LightTouchServices"]:
        subprocess.run(["codesign", "--force", "--sign", "-", str(path)], check=True)
    subprocess.run(["codesign", "--force", "--sign", "-", "--entitlements",
                    str(source / "Configuration/LightTouchDevice.entitlements"),
                    str(contents / "MacOS/LightTouchDevice")], check=True)
    subprocess.run(["codesign", "--force", "--sign", "-", str(output)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(output)], check=True)
    # Fingerprint final signed files rather than their intermediate link output.
    for category in ["rebuilt", "reused"]:
        for name in receipt[category]:
            receipt[category][name] = digest(contents / name)
    output.with_suffix(".build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()

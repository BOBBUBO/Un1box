#!/usr/bin/env python3
"""Compile our tiny iOS test app, or retain exact real-tool blocking evidence.

No SDK acquisition/network calls. All generated outputs must stay outside Git.
An IPA is emitted only after a real ARMv6 linker and ldid succeed and its legacy
Mach-O headers/signature pass inspection. Never treats an object file as an app.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import resource
import shutil
import struct
import subprocess
import time
import zlib
import zipfile

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inspect_macho(path):
    data = Path(path).read_bytes()
    if len(data) < 28:
        raise ValueError("Mach-O header truncated")
    magic, cpu, subtype, filetype, count, size, flags = struct.unpack_from("<7I", data)
    if magic != 0xfeedface or cpu != 12 or subtype != 6 or 28 + size > len(data):
        raise ValueError("Expected a complete 32-bit ARMv6 Mach-O")
    commands, cursor = [], 28
    for _ in range(count):
        command, length = struct.unpack_from("<II", data, cursor)
        if length < 8 or cursor + length > 28 + size:
            raise ValueError("Invalid Mach-O load-command extent")
        commands.append(command)
        cursor += length
    return {"cpu": cpu, "subtype": subtype, "filetype": filetype, "flags": flags,
            "commands": commands, "bytes": len(data), "sha256": sha(path)}


def tiny_limits():
    resource.setrlimit(resource.RLIMIT_CPU, (10, 15))
    resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2, 32 * 1024**2))


def run(command, out, results):
    start = time.monotonic()
    try:
        process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 timeout=20, preexec_fn=tiny_limits, close_fds=True)
        code, output = process.returncode, process.stdout
    except subprocess.TimeoutExpired as error:
        code, output = "TIMEOUT", error.stdout or b""
    log = out / ("{:02d}.log".format(len(results) + 1))
    log.write_bytes(output[:65536])
    log.chmod(0o600)
    result = {"command": command, "exit": code, "elapsedSeconds": time.monotonic() - start,
              "output": str(log), "outputSha256": sha(log)}
    results.append(result)
    return code == 0


def icon(path):
    """Original 57px icon, no external assets or image-library dependency."""
    rows = []
    for y in range(57):
        row = bytearray([0])
        for x in range(57):
            white = (12 <= x <= 17 and 13 <= y <= 43) or (12 <= x <= 30 and 38 <= y <= 43) or \
                    (29 <= x <= 47 and 13 <= y <= 18) or (36 <= x <= 41 and 13 <= y <= 43)
            row.extend((255, 255, 255) if white else (104, 39, 165))
        rows.append(bytes(row))
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 57, 57, 8, 2, 0, 0, 0)) +
                     chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


def build(args):
    out = Path(args.out).expanduser().resolve()
    repo = HERE.parents[2]
    if out == repo or repo in out.parents:
        raise ValueError("Generated app/evidence must be outside the source repository.")
    out.mkdir(parents=True, mode=0o700, exist_ok=False)
    results = []
    receipt = {"format": 1, "status": "UNFINISHED", "commands": results, "guestExecuted": False,
               "guestInstallQualified": False, "guestLaunchQualified": False,
               "source": {name: sha(HERE / name) for name in ("probe.c", "start.S", "libSystem.tbd", "build.py")},
               "signer": shutil.which(args.signer), "blockers": []}
    try:
        clang = shutil.which(args.clang)
        if not clang:
            receipt["blockers"].append("No clang compiler at " + args.clang)
            return receipt
        common = [clang, "--target=armv6-apple-ios3.1.3", "-marm", "-O1", "-ffixed-r9", "-ffreestanding",
                  "-fno-builtin", "-fno-stack-protector", "-nostdinc", "-Wall", "-Wextra", "-Werror"]
        for name in ("probe.c", "start.S"):
            obj = out / (name + ".o")
            if not run(common + ["-c", str(HERE / name), "-o", str(obj)], out, results):
                receipt["blockers"].append("Real ARMv6 compilation failed for " + name)
                return receipt
            receipt.setdefault("objects", {})[name] = inspect_macho(obj)
        linkers = args.linker or ["ld64.lld-18", "/home/blake/Android/Sdk/ndk/28.2.13676358/toolchains/llvm/prebuilt/linux-x86_64/bin/ld64.lld"]
        binary = out / "InstallProbe"
        for name in linkers:
            linker = shutil.which(name)
            if not linker:
                continue
            command = [linker, "-arch", "armv6", "-platform_version", "ios", "3.1.3", "3.1.3", "-no_pie",
                       "-no_uuid", "-no_function_starts", "-no_data_in_code_info", "-e", "_start",
                       "-L", str(HERE), "-lSystem", str(out / "start.S.o"), str(out / "probe.c.o"), "-o", str(binary)]
            if run(command, out, results):
                break
        else:
            receipt["blockers"].append("No available tested Mach-O linker accepted the real ARMv6 objects/legacy iOS target.")
            if not receipt["signer"]:
                receipt["blockers"].append("ldid signer is also unavailable on PATH; signing remains unattempted.")
            return receipt
        analysis = inspect_macho(binary)
        receipt["unsignedExecutable"] = analysis
        if analysis["filetype"] != 2 or 5 not in analysis["commands"] or 0x80000028 in analysis["commands"] or 0x80000022 in analysis["commands"]:
            receipt["blockers"].append("Linked executable lacks legacy LC_UNIXTHREAD or has unsupported modern dyld metadata.")
            return receipt
        if not receipt["signer"]:
            receipt["blockers"].append("ldid signer is unavailable; no unsigned IPA will be advertised as installable.")
            return receipt
        if not run([receipt["signer"], "-S", str(binary)], out, results):
            receipt["blockers"].append("Real ldid signing failed.")
            return receipt
        analysis = inspect_macho(binary)
        if 0x1d not in analysis["commands"]:
            receipt["blockers"].append("Signed output has no LC_CODE_SIGNATURE.")
            return receipt
        app = out / "Payload" / "InstallProbe.app"
        app.mkdir(mode=0o700, parents=True)
        shutil.copyfile(binary, app / "InstallProbe")
        (app / "InstallProbe").chmod(0o755)
        metadata = {"CFBundleIdentifier": "org.lighttouch.installprobe", "CFBundleExecutable": "InstallProbe",
                    "CFBundleDisplayName": "LT Probe", "CFBundleName": "InstallProbe", "CFBundleVersion": "1",
                    "CFBundlePackageType": "APPL", "CFBundleSupportedPlatforms": ["iPhoneOS"],
                    "CFBundleIconFile": "Icon.png", "LSRequiresIPhoneOS": True, "MinimumOSVersion": "3.1.3",
                    "UIDeviceFamily": [1], "UIStatusBarHidden": False}
        (app / "Info.plist").write_bytes(plistlib.dumps(metadata))
        icon(app / "Icon.png")
        ipa = out / "InstallProbe.ipa"
        with zipfile.ZipFile(ipa, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(app.iterdir()):
                archive.write(path, str(path.relative_to(out)))
        receipt.update(status="BUILT_SIGNED_UNQUALIFIED_IPA", executable=analysis,
                       ipa={"path": str(ipa), "sha256": sha(ipa), "bytes": ipa.stat().st_size})
        return receipt
    finally:
        if receipt["blockers"]:
            receipt["status"] = "BLOCKED_AVAILABLE_TOOLS"
        (out / "RESULT.json").write_text(json.dumps(receipt, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--clang", default="clang")
    parser.add_argument("--linker", action="append")
    parser.add_argument("--signer", default="ldid")
    args = parser.parse_args()
    receipt = build(args)
    print(json.dumps(receipt, indent=2))
    return 0 if receipt["status"] == "BUILT_SIGNED_UNQUALIFIED_IPA" else 1


if __name__ == "__main__":
    raise SystemExit(main())

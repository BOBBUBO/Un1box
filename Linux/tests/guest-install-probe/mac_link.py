#!/usr/bin/env python3
"""Bounded Mac tool experiment for our original SDK-free install probe.

Run only in an explicitly allocated NEW private staging directory containing
probe.c/start.S/libSystem.tbd/build.py and pinned upstream machotool.c.
Does not touch fixtures, launch a guest, acquire an SDK or install any package.
"""
import json
import os
from pathlib import Path
import plistlib
import resource
import signal
import subprocess
import time
import zipfile

from build import icon, inspect_macho, sha, verify_code_signature

MACHOTOOL_SHA256 = "372843eb2b5a23ff82790d7cf33ddb40ba8ee69caf81b002024798425abfced4"


def limits():
    resource.setrlimit(resource.RLIMIT_CPU, (10, 15))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2, 32 * 1024**2))


def run(command, directory, receipt, allow_failure=False):
    number = len(receipt["commands"]) + 1
    stdout = directory / ("{:02d}.log".format(number))
    start = time.monotonic()
    peak = 0
    violation = None
    with stdout.open("xb") as log:
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 close_fds=True, start_new_session=True, preexec_fn=limits)
        try:
            while child.poll() is None:
                # Darwin's RLIMIT_AS is unsuitable for modern dyld's virtual map.
                # Independently enforce <=1 GiB aggregate resident memory instead.
                listing = subprocess.run(["/bin/ps", "-axo", "pid=,ppid=,rss="], stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, timeout=2, check=True)
                rows = [tuple(map(int, line.split())) for line in listing.stdout.splitlines() if line.strip()]
                owned = {child.pid}
                while True:
                    new = owned | {pid for pid, ppid, rss in rows if ppid in owned}
                    if new == owned:
                        break
                    owned = new
                rss = sum(value * 1024 for pid, ppid, value in rows if pid in owned)
                peak = max(peak, rss)
                if rss > 1024**3 or time.monotonic() - start > 20:
                    violation = "rss-1GiB" if rss > 1024**3 else "outer-20s"
                    break
                time.sleep(.05)
        except Exception as error:
            violation = "monitor-failed: " + type(error).__name__ + ": " + str(error)
        finally:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            code = child.wait(timeout=3)
    result = {"command": list(command), "exit": code, "elapsedSeconds": time.monotonic() - start,
              "peakTreeRssBytes": peak, "violation": violation, "output": str(stdout), "outputSha256": sha(stdout)}
    receipt["commands"].append(result)
    if (code != 0 or violation) and not allow_failure:
        raise RuntimeError("A real tool operation failed; inspect " + str(stdout))
    return code == 0 and violation is None


def main():
    directory = Path(__file__).resolve().parent
    if not str(directory).startswith(("/tmp/lighttouch-install-probe.", "/private/tmp/lighttouch-install-probe.")):
        raise RuntimeError("Use a new private /tmp/lighttouch-install-probe.* staging directory.")
    if (directory / "MAC-RESULT.json").exists():
        raise RuntimeError("Refusing to overwrite an earlier attempt.")
    receipt = {"format": 1, "status": "UNFINISHED", "commands": [], "guestExecuted": False,
               "guestInstallQualified": False, "guestLaunchQualified": False,
               "inputs": {p.name: sha(p) for p in directory.iterdir() if p.is_file()}}
    try:
        if receipt["inputs"].get("machotool.c") != MACHOTOOL_SHA256:
            raise RuntimeError("The staged public machotool.c differs from the reviewed source.")
        empty_sysroot = directory / "empty-sysroot"
        empty_sysroot.mkdir(mode=0o700)
        compile_flags = ["/usr/bin/xcrun", "clang", "--target=armv6-apple-ios3.1.3", "-marm", "-O1", "-ffixed-r9",
                         "-isysroot", str(empty_sysroot), "-ffreestanding", "-fno-builtin", "-fno-stack-protector",
                         "-nostdinc", "-Wall", "-Wextra", "-Werror"]
        for name in ("probe.c", "start.S"):
            run(compile_flags + ["-c", str(directory / name), "-o", str(directory / (name + ".o"))], directory, receipt)
        run(["/usr/bin/xcrun", "clang", "-O1", "-std=c11", str(directory / "machotool.c"), "-o", str(directory / "machotool")], directory, receipt)
        # Test direct ARMv6 first; preserve an actual rejection before using
        # upstream's reviewed subtype-only ARMv7 link of genuine ARMv6 code.
        binary = directory / "InstallProbe"
        link = ["/usr/bin/xcrun", "ld", "-arch", "armv6", "-platform_version", "ios", "9.0", "9.0", "-no_pie",
                "-no_function_starts", "-no_data_in_code_info", "-no_uuid", "-e", "_start", "-L", str(directory),
                "-lSystem", str(directory / "start.S.o"), str(directory / "probe.c.o"), "-o", str(binary)]
        if not run(link, directory, receipt, allow_failure=True):
            for name in ("probe.c.o", "start.S.o"):
                run([str(directory / "machotool"), "subtype", str(directory / name), "9"], directory, receipt)
            stub = directory / "libSystem.tbd"
            original_stub = stub.read_text()
            (directory / "libSystem-armv6-original.tbd").write_text(original_stub)
            stub.write_text(original_stub.replace("[ armv6 ]", "[ armv7 ]"))
            link[3] = "armv7"
            run(link, directory, receipt)
        # n72 iOS3.1.3 supports compressed dyld info. --legacy is for 1.x/2.x
        # and rejects lazy binds, an unnecessary restriction for this fixture.
        run([str(directory / "machotool"), "mkold", str(binary), "--subtype", "6"], directory, receipt)
        analysis = inspect_macho(binary)
        if analysis["filetype"] != 2 or 5 not in analysis["commands"] or 0x80000028 in analysis["commands"]:
            raise RuntimeError("Legacy executable metadata admission failed.")
        run(["/opt/homebrew/bin/ldid", "-S", str(binary)], directory, receipt)
        analysis = inspect_macho(binary)
        if 0x1d not in analysis["commands"]:
            raise RuntimeError("No LC_CODE_SIGNATURE after signing.")
        receipt["codeSignature"] = verify_code_signature(binary)
        app = directory / "Payload" / "InstallProbe.app"
        app.mkdir(mode=0o700, parents=True)
        (app / "InstallProbe").write_bytes(binary.read_bytes())
        (app / "InstallProbe").chmod(0o755)
        info = {"CFBundleIdentifier": "org.lighttouch.installprobe", "CFBundleExecutable": "InstallProbe",
                "CFBundleDisplayName": "LT Probe", "CFBundleName": "InstallProbe", "CFBundleVersion": "1",
                "CFBundlePackageType": "APPL", "CFBundleSupportedPlatforms": ["iPhoneOS"], "CFBundleIconFile": "Icon.png",
                "LSRequiresIPhoneOS": True, "MinimumOSVersion": "3.1.3", "UIDeviceFamily": [1], "UIStatusBarHidden": False}
        (app / "Info.plist").write_bytes(plistlib.dumps(info))
        icon(app / "Icon.png")
        ipa = directory / "InstallProbe.ipa"
        with zipfile.ZipFile(ipa, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(app.iterdir()):
                archive.write(path, str(path.relative_to(directory)))
        receipt.update(status="BUILT_SIGNED_UNQUALIFIED_IPA", executable=analysis,
                       ipa={"path": str(ipa), "bytes": ipa.stat().st_size, "sha256": sha(ipa)})
    except Exception as error:
        receipt.update(status="FAILED_TOOL_EXPERIMENT", error=str(error))
    finally:
        (directory / "MAC-RESULT.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps(receipt, indent=2))
    return 0 if receipt["status"] == "BUILT_SIGNED_UNQUALIFIED_IPA" else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Plan or run one bounded canonical app-service check against an owned guest.

Never launches/stops a guest or daemon, sends input, opens a display, or changes
firmware. Install success here leaves icon/launch/touch/UI acceptance unqualified.
Only the current exclusive guest owner may opt into --execute.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time


BUNDLE = "org.lighttouch.installprobe"


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def private_socket(path):
    path = Path(path)
    info, parent = path.lstat(), path.parent.stat()
    if (not path.is_absolute() or path.is_symlink() or not stat.S_ISSOCK(info.st_mode) or
            info.st_uid != os.geteuid() or info.st_mode & 0o077 or
            parent.st_uid != os.geteuid() or parent.st_mode & 0o077):
        raise ValueError("Use the existing session Bridge's owner-only mux.sock.")
    return {"path": str(path), "device": info.st_dev, "inode": info.st_ino,
            "parent_device": parent.st_dev, "parent_inode": parent.st_ino}


def run_service(job_class, command, timeout, log, owns_install):
    job = job_class(command, timeout=timeout, owns_install=owns_install, log_path=log)
    # ServiceJob bounds the helper and retains all structured output. No native
    # guest/bridge process is signalled, including on an unconfirmed timeout.
    retirement_deadline = time.monotonic() + timeout + 5
    while job.active:
        if time.monotonic() >= retirement_deadline:
            raise RuntimeError("Helper did not retire after its bound; result unconfirmed, guest untouched by controller.")
        time.sleep(.05)
    job.thread.join(timeout=3)
    return {"successful": job.successful(), "exit": job.child.wait(timeout=3),
            "target": job.target, "result": job.result, "error": job.error,
            "log": str(log), "log_sha256": sha(log)}


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--action", choices=("info", "install"), default="info")
    parser.add_argument("--linux-source", type=Path,
                        help="Exact Linux directory or AppDir/usr/share/lighttouch containing app_services")
    parser.add_argument("--socket", type=Path)
    parser.add_argument("--library", type=Path)
    parser.add_argument("--plist-library", type=Path)
    parser.add_argument("--ipa", type=Path)
    parser.add_argument("--expected-ipa-sha256")
    parser.add_argument("--evidence", type=Path, help="NEW private directory outside every Git checkout")
    parser.add_argument("--product", default="iPod2,1")
    parser.add_argument("--ios", default="3.1.3")
    parser.add_argument("--arch", choices=("armv6", "armv7"), default="armv6")
    return parser.parse_args()


def execute(args, receipt):
    required = ("linux_source", "socket", "library", "plist_library", "evidence")
    if any(getattr(args, key) is None for key in required):
        raise ValueError("Execution requires explicit source, existing bridge socket, libraries and evidence paths.")
    for path in (args.linux_source, args.socket, args.library, args.plist_library, args.evidence):
        if not path.is_absolute():
            raise ValueError("Every execution path must be absolute.")
    source = args.linux_source.resolve()
    if not (source / "app_services" / "helper.py").is_file():
        raise ValueError("The explicit app_services source directory is missing.")
    for path in (args.library, args.plist_library):
        if not path.is_file():
            raise ValueError("An explicit native library is missing: " + str(path))
    socket_identity = private_socket(args.socket)
    evidence = args.evidence.resolve()
    if any((parent / ".git").exists() for parent in (evidence,) + tuple(evidence.parents)):
        raise ValueError("Evidence must remain outside every Git checkout.")
    sys.path.insert(0, str(source))
    from app_services.bridge import ServiceJob
    from app_services.catalog import Target
    from app_services.ipa import inspect

    expected = {"product_type": args.product, "ios": args.ios, "arch": args.arch, "family": "1"}
    target = Target(**expected)
    if args.action == "install":
        if args.ipa is None or not args.ipa.is_absolute() or not args.expected_ipa_sha256:
            raise ValueError("Install requires the exact original IPA and its independently reviewed SHA-256.")
        if sha(args.ipa) != args.expected_ipa_sha256:
            raise ValueError("The IPA differs from the reviewed artifact.")
        metadata = inspect(args.ipa, target)
        if metadata["bundle_id"] != BUNDLE:
            raise ValueError("This acceptance controller only installs the original LT Probe.")
        receipt["ipa"] = {"path": str(args.ipa), "sha256": sha(args.ipa), "metadata": metadata}
    evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    receipt.update(status="EXECUTING", socket=socket_identity,
                   sources={p.name: sha(p) for p in sorted((source / "app_services").glob("*.py"))},
                   libraries={str(p): sha(p) for p in (args.library, args.plist_library)})
    output = evidence / "RESULT.json"

    def save():
        output.write_text(json.dumps(receipt, indent=2) + "\n")

    def command(action):
        return [sys.executable, "-m", "app_services.helper", action, "--socket", str(args.socket),
                "--library", str(args.library), "--plist-library", str(args.plist_library),
                "--scratch", str(evidence / "scratch")]

    try:
        receipt["helperMayBeStarted"] = True
        save()
        info = run_service(ServiceJob, command("info"), 30, evidence / "info.jsonl", False)
        receipt["info"] = info
        if not info["successful"] or info["target"] != expected or info["result"] != {"ready": True}:
            raise RuntimeError("Info did not confirm the exact expected guest; no install was submitted.")
        if args.action == "info":
            receipt["status"] = "GUEST_SERVICE_INFO_VERIFIED_VISUAL_UNQUALIFIED"
            return receipt
        if private_socket(args.socket) != socket_identity:
            raise RuntimeError("Bridge socket identity changed after info; no install was submitted.")
        # Conservative ownership starts before helper creation, as in AppsWindow.
        receipt["installMayBeSubmitted"] = True
        save()
        install = run_service(ServiceJob, command("install") + ["--ipa", str(args.ipa)],
                              900, evidence / "install.jsonl", True)
        receipt["install"] = install
        if (not install["successful"] or install["target"] != expected or
                not isinstance(install["result"], dict) or
                install["result"].get("bundle_id") != BUNDLE or install["result"].get("installed") is not True):
            raise RuntimeError("Install service did not confirm the expected app; inspect before retrying.")
        receipt["status"] = "INSTALL_SERVICE_VERIFIED_VISUAL_UNQUALIFIED"
        return receipt
    except Exception as error:
        receipt.update(status="FAILED_OR_UNCONFIRMED", error=str(error))
        return receipt
    finally:
        receipt["finished_monotonic_ns"] = time.monotonic_ns()
        save()


def main():
    args = arguments()
    receipt = {"format": 1, "status": "PLAN_ONLY", "action": args.action,
               "started_monotonic_ns": time.monotonic_ns(), "helperMayBeStarted": False,
               "installMayBeSubmitted": False,
               "guestBootQualified": False, "frontendInstallUIQualified": False,
               "iconQualified": False, "launchQualified": False, "touchQualified": False,
               "shutdownQualified": False,
               "ownerRequirements": ["Exclusive guest ownership; preserve base and isolated writable state",
                  "Start canonical Bridge before the fresh worker (IT_USB_TCP); no after-start attachment",
                  "No other Apps helper or Stop/Close while this controller is active",
                  "After service exit inspect actual Home/icon, tap icon, inspect purple app, tap and inspect blue",
                  "Use canonical launcher Stop and require strict clean_exit; controller never stops the guest"]}
    if args.execute:
        try:
            receipt = execute(args, receipt)
        except Exception as error:
            status = "FAILED_OR_UNCONFIRMED" if receipt["helperMayBeStarted"] else "PRECONDITION_REJECTED_NO_HELPER_STARTED"
            receipt.update(status=status, error=str(error))
    print(json.dumps(receipt, indent=2))
    return 1 if receipt["status"] in ("FAILED_OR_UNCONFIRMED", "PRECONDITION_REJECTED_NO_HELPER_STARTED") else 0


if __name__ == "__main__":
    raise SystemExit(main())

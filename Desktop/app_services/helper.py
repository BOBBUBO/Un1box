"""Private subprocess entry point. No system usbmux or physical-device fallback."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import tempfile
import re

from .ipa import prepare
from .mobile import Mobile


def emit(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["info", "install"])
    parser.add_argument("--socket", required=True)
    parser.add_argument("--library", required=True)
    parser.add_argument("--plist-library", required=True)
    parser.add_argument("--scratch", required=True)
    parser.add_argument("--ipa")
    parser.add_argument("--daemon-pid")
    parser.add_argument("--daemon-created")
    args = parser.parse_args()
    path = Path(args.socket)
    try:
        if os.name == "nt":
            # Owned libusbmuxd verifies the actual reverse TCP owner + SID +
            # exact process creation time before sending any protocol bytes.
            match = re.fullmatch(r"127\.0\.0\.1:([1-9][0-9]{0,4})", args.socket)
            if (not match or int(match[1]) > 65535 or
                    not re.fullmatch(r"[1-9][0-9]{0,9}", args.daemon_pid or "") or
                    int(args.daemon_pid) > 2**32-1 or
                    not re.fullmatch(r"[1-9][0-9]{0,19}", args.daemon_created or "") or
                    int(args.daemon_created) > 2**64-1):
                raise ValueError("An exact owned Windows guest-service endpoint is required.")
            expected = {"USBMUXD_SOCKET_ADDRESS": args.socket, "LTW_USBMUX_PID": args.daemon_pid,
                        "LTW_USBMUX_CREATED": args.daemon_created}
            if any(os.environ.get(key) != value for key, value in expected.items()):
                raise ValueError("Guest service launch environment differs from its owned endpoint.")
            from lighttouch_desktop.windows_storage import WindowsStorage
            storage = WindowsStorage()
            storage.ensure_directory(args.scratch)
        else:
            info = path.lstat()
            parent = path.parent.stat()
            if (not path.is_absolute() or path.is_symlink() or not stat.S_ISSOCK(info.st_mode) or
                    info.st_uid != os.geteuid() or info.st_mode & 0o077 or
                    parent.st_uid != os.geteuid() or parent.st_mode & 0o077):
                raise ValueError("A private owner-only guest usbmux socket is required.")
            os.environ["USBMUXD_SOCKET_ADDRESS"] = "UNIX:" + str(path)
        mobile = Mobile(args.library, args.plist_library)
        try:
            target = mobile.target()
            emit({"target": asdict(target)})
            if args.action == "install":
                if not args.ipa:
                    raise ValueError("Choose an IPA.")
                scratch = Path(args.scratch)
                scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
                temporary = Path(tempfile.mkdtemp(prefix="install-", dir=str(scratch)))
                try:
                    emit({"phase": "prepare", "status": "Checking app and executable permissions"})
                    prepared = temporary / "App.ipa"
                    metadata = prepare(args.ipa, prepared, target)
                    result = mobile.install(prepared, metadata["bundle_id"], emit)
                    emit({"result": result})
                finally:
                    shutil.rmtree(temporary)
            else:
                emit({"result": {"ready": True}})
        finally:
            mobile.close()
        return 0
    except Exception as error:
        emit({"error": str(error)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

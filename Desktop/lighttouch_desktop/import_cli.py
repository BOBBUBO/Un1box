"""Shared streamed import, followed by narrow native admission/publication.

Run with the bundled interpreter by absolute script path, including under -I.
Native helpers finalize the staged base in place; no second NAND copy is made.
"""
import argparse
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lighttouch_desktop.profiles import SetupError, disjoint, regular
from lighttouch_desktop.storage import DEFAULT_STORAGE


def import_command(platform, frontend, source, destination, sha256, *, storage=None, validation_cache=None):
    """One desktop import implementation, retaining native filesystem policy."""
    import re
    storage = storage or DEFAULT_STORAGE
    source, destination = Path(source).absolute(), Path(destination).absolute()
    regular(source, "iOS system archive (.itbase)", storage=storage)
    regular(frontend, "native import finalizer", storage=storage)
    if not isinstance(sha256, str) or not re.fullmatch("[a-fA-F0-9]{64}", sha256):
        raise SetupError("The selected archive requires its calculated SHA-256.")
    if destination.exists() or destination.is_symlink():
        raise SetupError("Import destination already exists. Choose a new folder.")
    disjoint(source, destination)
    command = [sys.executable, "-I", str(Path(__file__).resolve()),
               "--platform", platform, "--frontend", str(Path(frontend).absolute()),
               "--source", str(source), "--destination", str(destination), "--sha256", sha256.lower()]
    if validation_cache is not None:
        command += ["--validation-cache", str(Path(validation_cache).absolute())]
    return command


def _remove_stage(stage):
    # A cancelled finalizer may already have made the unpublished files read-only.
    # Touch only this invocation's unique sibling stage, never a published base.
    def retry(function, path, _error):
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        function(path)
    if stage.exists():
        os.chmod(stage, 0o700)
        for directory, _, _ in os.walk(stage):
            os.chmod(directory, 0o700)
        shutil.rmtree(stage, onerror=retry)


def run(args, *, storage=None, cancelled=None):
    from lighttouch_desktop.prepared_import import ImportCancelled, import_prepared
    storage = storage or DEFAULT_STORAGE
    cancelled = cancelled or threading.Event()
    destination = Path(args.destination).absolute()
    storage.ensure_directory(destination.parent)
    if destination.exists() or destination.is_symlink():
        raise SetupError("Import destination already exists. Existing files were retained.")
    disjoint(args.source, destination)
    stage = destination.parent / (".lighttouch-import-" + uuid.uuid4().hex)
    print("[import-stage] " + str(stage), flush=True)
    started = time.monotonic()
    last = [None, 0.0]

    def progress(phase, done, total):
        # Keep the existing shared launcher's progress protocol and durable log.
        phase = {"packing": "verifying", "complete": "verifying"}.get(phase, phase)
        now = time.monotonic()
        if last[0] != phase or done == total or now - last[1] >= .1:
            print("import phase=%s percent=%d done=%d total=%d" %
                  (phase, min(100, 100 * done // max(1, total)), done, total), flush=True)
            last[:] = [phase, now]

    try:
        import_prepared(Path(args.source), stage, args.sha256, storage=storage,
                        progress=progress, cancelled=cancelled.is_set)
        print("[import-timing] phase=streaming seconds=%.3f" % (time.monotonic() - started), flush=True)
        if cancelled.is_set():
            raise ImportCancelled("Setup cancelled. Your original files are unchanged.")
        if args.platform == "linux":
            command = [args.frontend, "--finalize-import", str(stage), "--base", str(destination)]
        else:
            command = [args.frontend, "finalize-import", "--stage", str(stage), "--destination", str(destination)]
            if args.platform == "windows" and args.validation_cache:
                command += ["--validation-cache", args.validation_cache]
        print("[import] Checking and publishing packed base in place.", flush=True)
        child = subprocess.Popen(command, stdin=subprocess.PIPE, close_fds=True)
        stopping = False
        try:
            while child.poll() is None:
                if cancelled.is_set() and not stopping:
                    stopping = True
                    if args.platform == "linux":
                        child.send_signal(signal.SIGINT)
                    else:
                        try:
                            child.stdin.write(b"stop\n"); child.stdin.flush()
                        except (BrokenPipeError, OSError):
                            pass
                try:
                    child.wait(timeout=.1)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            child.stdin.close()
        if child.returncode:
            raise SetupError("Native system validation failed (exit %d). See the import log." % child.returncode)
        if stage.exists() or not destination.is_dir():
            raise SetupError("The native importer did not publish the completed system.")
        print("[import-timing] phase=total seconds=%.3f" % (time.monotonic() - started), flush=True)
        return 0
    finally:
        try:
            _remove_stage(stage)
        except OSError as error:
            print("LightTouch: Incomplete import retained at %s; cleanup failed: %s" %
                  (stage, error), file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser(description="LightTouch shared prepared-system importer")
    parser.add_argument("--platform", required=True, choices=("linux", "windows", "macos"))
    for key in ("source", "destination", "sha256", "frontend"):
        parser.add_argument("--" + key, required=True)
    parser.add_argument("--validation-cache")
    args = parser.parse_args()
    cancelled = threading.Event()
    def stop(_number=None, _frame=None):
        cancelled.set()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    def controls():
        # A daemon blocked in sys.stdin.readline would hold its buffered-IO
        # lock during Python finalization when the launcher keeps stdin open.
        # Raw descriptor reads have no Python stream lock to abort on exit.
        pending = b""
        try:
            while True:
                block = os.read(0, 4096)
                if not block:
                    return
                pending += block
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    if line.strip() == b"stop":
                        cancelled.set()
                        return
                if len(pending) > 4096:
                    pending = b""
        except OSError:
            return
    threading.Thread(target=controls, daemon=True).start()
    storage = DEFAULT_STORAGE
    if args.platform == "windows":
        from lighttouch_desktop.windows_storage import WindowsStorage
        storage = WindowsStorage()
    try:
        return run(args, storage=storage, cancelled=cancelled)
    except Exception as error:
        print("LightTouch: " + str(error), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

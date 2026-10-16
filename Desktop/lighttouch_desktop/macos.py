"""macOS adapter for the maintained shared launcher and native session runner.

Saved JSON profiles retain the existing desktop format. DeviceInstance plist
creation/admission stays in the native runner; this module never writes or
migrates an installed Cocoa library.
"""
from collections import deque
import json
import os
from pathlib import Path
import plistlib
import queue
import re
import subprocess
import threading
import uuid

from .profiles import (FORMAT, VERSION, Profiles, SetupError, absolute, display_title,
                       disjoint, inspect_base, load_profile, regular, save_json, validate)
from .profiles import read_json
from .preparation import Preparation


def managed_record(profile):
    """Read-only mapping into this configuration's isolated native state root."""
    info = validate(profile, allow_dirty=True)
    identity = profile.get("id", "")
    if not isinstance(identity, str) or not re.fullmatch("[a-f0-9]{32}", identity):
        raise SetupError("Choose a locally saved device configuration with a valid identity.")
    root = Path(absolute(profile["state"]))
    native_id = str(uuid.UUID(hex=identity)).upper()
    directory = root / "Devices" / native_id
    for path in (root, root / "Devices", directory, directory / "work"):
        if path.is_symlink():
            raise SetupError("Native writable state cannot use symbolic-link containers.")
    record = directory / "device.plist"
    if record.exists() or record.is_symlink():
        regular(record, "native device record")
        if record.stat().st_size > 1024**2:
            raise SetupError("Native device record exceeds 1 MiB.")
        try:
            value = plistlib.loads(record.read_bytes())
            same = (value.get("format") == 1 and value.get("id") == native_id and
                    value.get("board") == info["board"] and
                    Path(value["base"]["path"]).resolve() == Path(profile["base"]).resolve())
        except (ValueError, TypeError, KeyError, AttributeError, plistlib.InvalidFileException) as error:
            raise SetupError("Native device record is invalid. Its files were retained.") from error
        if not same:
            raise SetupError("This state belongs to another native device or system. Select its saved configuration.")
    elif root.exists() and any(p.name not in (".shared-session-lease",) for p in root.iterdir()):
        raise SetupError("New native state must be empty. Existing Linux or Cocoa state is never converted automatically.")
    return dict(info, native_id=native_id, record=str(record), native_state=str(root))


def clean_exit(code, receipt):
    expected = {"status_available": "1", "state": "0", "confirmed": "1", "dirty": "0",
                "worker_reaped": "1", "worker_exit": "0", "worker_disposal_requested": "0", "frontend_return": "0"}
    return code == 0 and receipt is not None and all(receipt.get(k) == v for k, v in expected.items())


class MacSession:
    """Fresh native host process per session; Stop uses its owned stdin pipe.

    No /proc dependency, PID-based signal or in-process QEMU initialization.
    Terminal receipts count only after the owned child and its output are reaped.
    """
    def __init__(self, application_log=None):
        self.child = None
        self.queue = queue.Queue(maxsize=256)
        self.tail = deque(maxlen=100)
        self.reader_done = threading.Event()
        self.log_path = None
        self.application_log = Path(application_log) if application_log else None
        self.final = None
        self.status = {}
        self.identity = None
        self.returncode = None
        self.stopping = False
        self.errors = deque(maxlen=32)

    @property
    def active(self):
        return self.child is not None and (self.child.poll() is None or not self.reader_done.is_set())

    def record_error(self, component, error):
        line = "APPLICATION_ERROR " + json.dumps({"component": str(component)[:128], "error": str(error)[:8192]}) + "\n"
        self.errors.append(line)
        if self.application_log:
            self.application_log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(str(self.application_log), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "a") as stream:
                if stream.tell() < 1024**2:
                    stream.write(line)

    def diagnostic_report(self):
        text = "LightTouch shared macOS launcher\nNative log: " + str(self.log_path) + "\n"
        if self.log_path and self.log_path.is_file():
            with self.log_path.open("rb") as stream:
                text += stream.read(65536).decode("utf-8", "replace")
                size = stream.seek(0, os.SEEK_END)
                if size > 65536:
                    stream.seek(max(65536, size - 65536))
                    text += "\n--- Recent output ---\n" + stream.read(65536).decode("utf-8", "replace")
        return text + "\n" + "".join(self.errors)

    def start(self, command, log_path, env=None):
        if self.active:
            raise RuntimeError("Stop the current device before starting another session.")
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(str(self.log_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        log = os.fdopen(fd, "wb")
        environment = dict(os.environ if env is None else env)
        # Experimental cache is unqualified and must not inherit an opt-in.
        for key in ("LT_TCG_IR_CACHE_ENABLE", "LT_TCG_IR_CACHE", "LTM_QEMU_DYLIB"):
            environment.pop(key, None)
        self.final = None; self.status = {}; self.identity = None
        self.returncode = None; self.stopping = False
        self.queue = queue.Queue(maxsize=256); self.tail.clear(); self.reader_done.clear()
        try:
            self.child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, close_fds=True, env=environment)
        except BaseException:
            log.close(); self.reader_done.set(); raise
        threading.Thread(target=self._read, args=(self.child, log), daemon=True).start()

    def _read(self, child, log):
        size = 0
        try:
            while True:
                raw = child.stdout.readline(65536)
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").rstrip()
                if size < 64 * 1024**2 or line.startswith(("FINAL ", "LightTouch:")):
                    log.write(raw); size += len(raw)
                try:
                    self.queue.put_nowait(line)
                except queue.Full:
                    try:
                        self.queue.get_nowait()
                    except queue.Empty:
                        pass
                    self.queue.put_nowait(line)
        finally:
            child.stdout.close(); log.close(); self.reader_done.set()

    def poll(self):
        result = []
        while True:
            try:
                line = self.queue.get_nowait()
            except queue.Empty:
                break
            self.tail.append(line); result.append(line)
            values = dict(re.findall(r"\b([a-zA-Z_][a-zA-Z_0-9]*)=([^\s]+)", line))
            if line.startswith("status "):
                self.status = values
            if line.startswith("FINAL "):
                self.final = values
        if self.child and not self.active:
            self.returncode = self.child.wait()
            if self.child.stdin:
                self.child.stdin.close()
        return result

    def stop(self):
        if not self.child or self.child.poll() is not None or self.stopping:
            return
        try:
            self.child.stdin.write(b"stop\n"); self.child.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise RuntimeError("The native session control pipe closed; state was retained.") from error
        self.stopping = True

    def metrics(self):
        # A missing native measurement is unavailable, never a fabricated zero.
        return {"host": {}, "worker": None, "worker_status": "Emulator and host CPU/RAM: unavailable on this host"}


class MacHost:
    font_family = "Helvetica Neue"
    open_profiles = staticmethod(Profiles)
    create_session = staticmethod(MacSession)
    inspect_base = staticmethod(inspect_base)
    display_title = staticmethod(display_title)
    prepare = staticmethod(Preparation)
    clean_exit = staticmethod(clean_exit)

    @staticmethod
    def load_profile(path):
        profile = load_profile(path)
        original = read_json(path)
        if "id" in original:
            profile["id"] = original["id"]
        managed_record(profile)
        return profile

    @staticmethod
    def validate(profile, allow_dirty=False):
        info = validate(profile, allow_dirty=allow_dirty)
        managed_record(profile)
        return info

    @staticmethod
    def export_profile(path, profile):
        save_json(path, dict(profile, format=FORMAT, version=VERSION))

    @staticmethod
    def _runner(args):
        regular(args.frontend, "shared macOS native host")
        if not os.access(args.frontend, os.X_OK):
            raise SetupError("The shared macOS native host is not executable.")
        return [str(args.frontend)]

    @classmethod
    def guest_command(cls, args, profile, recover):
        cls.validate(profile, allow_dirty=recover)
        for path, label in ((args.engine, "native engine"), (args.helper, "native device helper"),
                            (args.firmwarekit, "native firmware admission worker")):
            regular(path, label)
        command = cls._runner(args) + ["run", "--id", profile["id"], "--name", profile["name"],
                  "--base", absolute(profile["base"]), "--state", absolute(profile["state"]),
                  "--package", absolute(profile["guest_package"]), "--engine", str(Path(args.engine).absolute()),
                  "--helper", str(Path(args.helper).absolute()), "--firmwarekit", str(Path(args.firmwarekit).absolute())]
        if profile.get("bootrom"):
            command += ["--rom", absolute(profile["bootrom"])]
        if recover:
            command += ["--recover"]
        if not profile.get("audio", True):
            command += ["--no-audio"]
        return command

    @classmethod
    def import_command(cls, args, source, destination, sha256):
        regular(source, "prepared base archive")
        if not re.fullmatch("[a-fA-F0-9]{64}", sha256):
            raise SetupError("The prepared archive needs its calculated SHA-256.")
        destination = Path(destination).absolute()
        if destination.exists() or destination.is_symlink():
            raise SetupError("Import destination already exists. Choose a new folder.")
        disjoint(source, destination)
        return cls._runner(args) + ["import", "--source", absolute(source), "--destination", str(destination),
                                   "--sha256", sha256.lower()]

    @staticmethod
    def start_services(args, directory, identity):
        # Guest app-services integration is a separate unqualified native slice.
        return None, None

    @staticmethod
    def apps_window(*args, **kwargs):
        raise SetupError("Guest app services are not available in this shared macOS development build.")

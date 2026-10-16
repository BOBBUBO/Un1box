"""Owned Windows guest bridge and readiness bound to its held process handle.

Each saved device gets its own QEMU-only daemon and private pairing state.
The native connector checks TCP peer ownership before sending protocol bytes.
Host or Wine tests do not establish physical Windows guest acceptance.
"""
from dataclasses import dataclass
import json
import re


class ServiceError(RuntimeError):
    pass


def _identity(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value) is not None


def _number(value, maximum):
    return type(value) is int and 0 < value <= maximum


def _created(value):
    return (isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,19}", value) is not None
            and int(value) <= 2**64 - 1)


def _unique(pairs):
    fields = {}
    for key, value in pairs:
        if key in fields:
            raise ServiceError("Duplicate native daemon receipt field.")
        fields[key] = value
    return fields


@dataclass(frozen=True)
class Endpoint:
    pid: int
    created: str
    client_port: int
    guest_port: int


class DaemonSession:
    """Bind one readiness line to an already owned, still-live child handle.

    `live_identity` must return `(pid, decimal FILETIME)` from the held child
    process handle, or None after its exit. A fresh launch token is an identity
    tag, not a transport secret. The connector independently admits the peer.
    """
    def __init__(self, *, profile, token, generation, previous_generation, pid, created, live_identity):
        if (not _identity(profile) or not _identity(token) or
                not _number(generation, 2**53 - 1) or type(previous_generation) is not int or
                not 0 <= previous_generation < generation or not _number(pid, 2**32 - 1) or
                not _created(created) or not callable(live_identity)):
            raise ServiceError("Invalid owned daemon session identity.")
        self.profile = profile
        self.token = token
        self.generation = generation
        self.pid = pid
        self.created = created
        self._live_identity = live_identity
        self._endpoint = None
        self._state = "starting"

    def _live(self):
        try:
            identity = self._live_identity()
        except Exception:
            identity = None
        return identity == (self.pid, self.created)

    def accept_ready(self, line):
        """A malformed/duplicate/stale line permanently invalidates this launch."""
        try:
            if self._state != "starting" or not self._live():
                raise ServiceError("Daemon readiness does not belong to this live launch.")
            if not isinstance(line, str) or len(line) > 4096 or not line.startswith("DAEMON "):
                raise ServiceError("Missing or oversized daemon readiness receipt.")
            try:
                fields = json.loads(line[7:], object_pairs_hook=_unique)
            except (ValueError, TypeError) as failure:
                raise ServiceError("Malformed native daemon readiness receipt.") from failure
            required = {"format", "version", "pid", "created", "client_port", "guest_port",
                        "boundary", "profile", "token", "generation"}
            if not isinstance(fields, dict) or set(fields) != required:
                raise ServiceError("Unexpected native daemon readiness fields.")
            if (fields["format"] != "lighttouch-windows-usbmux" or type(fields["version"]) is not int or
                    fields["version"] != 1 or fields["boundary"] != "windows-owner-v1" or
                    fields["profile"] != self.profile or fields["token"] != self.token or
                    type(fields["generation"]) is not int or fields["generation"] != self.generation or
                    type(fields["pid"]) is not int or fields["pid"] != self.pid or fields["created"] != self.created or
                    not _number(fields["client_port"], 65535) or not _number(fields["guest_port"], 65535) or
                    fields["client_port"] == fields["guest_port"] or not self._live()):
                raise ServiceError("Daemon readiness identity, boundary or ports differ.")
            self._endpoint = Endpoint(self.pid, self.created, fields["client_port"], fields["guest_port"])
            self._state = "ready"
        except Exception:
            self._endpoint = None
            self._state = "failed"
            raise

    def environment(self):
        """Exact per-job environment, never a system/physical fallback."""
        if self._state != "ready":
            raise ServiceError("The owned guest service is unavailable or stopping.")
        if self._endpoint is None or not self._live():
            self._endpoint = None
            self._state = "failed"
            raise ServiceError("The owned guest service is unavailable or stopping.")
        return {"USBMUXD_SOCKET_ADDRESS": f"127.0.0.1:{self._endpoint.client_port}",
                "LTW_USBMUX_PID": str(self.pid), "LTW_USBMUX_CREATED": self.created,
                "IT_USB_TCP": f"127.0.0.1:{self._endpoint.guest_port}"}

    def request_stop(self):
        # Invalidate availability BEFORE the parent writes to its owned pipe.
        self._endpoint = None
        if self._state not in ("retired", "failed"):
            self._state = "stopping"

    def finish(self, *, exit_code, retirement_line, reaped, output_closed):
        # A text line alone is never retirement; no PID lookup/kill fallback.
        clean = (self._state == "stopping" and type(exit_code) is int and exit_code == 0 and
                 reaped is True and output_closed is True and
                 retirement_line == "DAEMON_RETIRED listeners_closed=1 control_closed=1" and
                 not self._live())
        self._endpoint = None
        self._state = "retired" if clean else "failed"
        return clean


# The launcher owns this exact foreground child. No physical USB daemon is used.
import ctypes
from ctypes import wintypes as wt
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

from app_services.ipa import InstallError


def child_identity(child):
    if os.name != "nt" or child.poll() is not None:
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_times = kernel.GetProcessTimes
    get_times.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4
    get_times.restype = wt.BOOL
    created, exited, system, user = (wt.FILETIME() for _ in range(4))
    # Popen retains this native process handle, including its original identity.
    if not get_times(wt.HANDLE(int(child._handle)), ctypes.byref(created), ctypes.byref(exited),
                     ctypes.byref(system), ctypes.byref(user)):
        raise InstallError("Cannot identify the owned guest app-service process.")
    stamp = (created.dwHighDateTime << 32) | created.dwLowDateTime
    return child.pid, str(stamp)


class WindowsBridge:
    def __init__(self, runtime, directory, identity, storage):
        self.runtime, self.directory = Path(runtime).absolute(), Path(directory).absolute()
        self.identity, self.storage = identity, storage
        self.child = self.session = self.log = None
        self.events = queue.Queue()
        self.output_done = threading.Event()
        self.retirement = None
        self.closed = False
        self.pins = []

    def _read(self):
        try:
            for raw in iter(self.child.stdout.readline, b""):
                self.log.write(raw); self.log.flush()
                line = raw.decode("utf-8", "replace").rstrip()
                if line.startswith("DAEMON "):
                    self.events.put(line)
                elif line == "DAEMON_RETIRED listeners_closed=1 control_closed=1":
                    self.retirement = line
        finally:
            self.child.stdout.close()
            self.output_done.set()

    def start(self):
        if not _identity(self.identity):
            raise InstallError("Select a saved device before starting app services.")
        for name in ("lighttouch-usbmuxd.exe", "libimobiledevice-1.0.dll", "libplist-2.0.dll"):
            with os.fdopen(self.storage.open_regular(self.runtime / name), "rb"):
                pass
        pairing = self.directory / "pairing" / self.identity
        logs = self.directory / "service-logs"
        for path in (pairing, logs):
            self.storage.ensure_directory(path)
            # Pin private folders across daemon lifetime on the native backend.
            if hasattr(self.storage, "_directories"):
                pin = self.storage._directories(path, private=True)
                pin.__enter__(); self.pins.append(pin)
        token = uuid.uuid4().hex
        environment = dict(os.environ)
        for key in list(environment):
            if key.upper().startswith("LTW_") or key.upper() in ("IT_USB_TCP", "USBMUXD_SOCKET_ADDRESS", "USBMUXD_QEMU_ADDR"):
                environment.pop(key)
        environment.update(LTW_SESSION_PROFILE=self.identity, LTW_SESSION_TOKEN=token, LTW_SESSION_GENERATION="1")
        self.log_path = logs / ("usbmux-" + token + ".log")
        self.log = self.storage.open_log(self.log_path, exclusive=True)
        try:
            self.child = subprocess.Popen([str(self.runtime / "lighttouch-usbmuxd.exe"), str(pairing)],
                                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                                          close_fds=True, env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            threading.Thread(target=self._read, daemon=True).start()
            live = child_identity(self.child)
            if live is None:
                raise InstallError("Guest app-service process exited before readiness.")
            self.session = DaemonSession(profile=self.identity, token=token, generation=1, previous_generation=0,
                                         pid=live[0], created=live[1], live_identity=lambda: child_identity(self.child))
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                try:
                    self.session.accept_ready(self.events.get(timeout=.05))
                    return self.session.environment()
                except queue.Empty:
                    if self.child.poll() is not None:
                        break
            raise InstallError("Guest app-service startup failed. See " + str(self.log_path))
        except BaseException:
            self.close()
            raise

    @property
    def alive(self):
        if self.closed or self.session is None:
            return False
        try:
            self.session.environment()
            # Repeated readiness is a protocol failure, not another daemon.
            if not self.events.empty():
                self.session.accept_ready(self.events.get_nowait())
            return True
        except (ServiceError, OSError):
            return False

    def service_environment(self):
        if not self.alive:
            raise InstallError("The guest app bridge has stopped. Start a fresh iOS session.")
        return self.session.environment()

    def command(self, action, scratch, ipa=None):
        if action not in ("info", "install") or (action == "install") != (ipa is not None):
            raise InstallError("Choose a supported guest app operation and IPA.")
        env = self.service_environment()
        self.storage.ensure_directory(scratch)
        # The launcher uses pythonw; helpers need working protocol stdout.
        interpreter = Path(sys.executable).with_name("python.exe")
        command = [str(interpreter) if interpreter.is_file() else sys.executable, "-m", "app_services.helper", action,
                   "--socket", env["USBMUXD_SOCKET_ADDRESS"], "--daemon-pid", env["LTW_USBMUX_PID"],
                   "--daemon-created", env["LTW_USBMUX_CREATED"],
                   "--library", str(self.runtime / "libimobiledevice-1.0.dll"),
                   "--plist-library", str(self.runtime / "libplist-2.0.dll"), "--scratch", str(scratch)]
        if ipa is not None:
            command += ["--ipa", str(ipa)]
        return command

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.session:
            self.session.request_stop()
        try:
            if self.child:
                if self.child.poll() is None:
                    try:
                        self.child.stdin.write(b"stop\n"); self.child.stdin.flush()
                    except (OSError, ValueError):
                        pass
                    try:
                        self.child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        # Service child has no NAND writer. Retire only our own
                        # process if its cooperative control failed.
                        self.child.kill(); self.child.wait(timeout=3)
                self.output_done.wait(timeout=3)
                try:
                    self.child.stdin.close()
                except OSError:
                    pass
                clean = self.session and self.session.finish(exit_code=self.child.returncode,
                    retirement_line=self.retirement, reaped=True, output_closed=self.output_done.is_set())
                if not clean and self.log:
                    self.log.write(b"APPLICATION_ERROR guest app-service retirement unconfirmed\n")
        finally:
            if self.log:
                self.log.close()
            for pin in reversed(self.pins):
                pin.__exit__(None, None, None)
            self.pins.clear()

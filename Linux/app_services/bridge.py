"""Session-owned QEMU-only usbmux daemon and finite service subprocesses."""
import json
import os
from pathlib import Path
import queue
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time

from .ipa import InstallError


class Bridge:
    def __init__(self, daemon, library, plist_library, pairing, logs):
        self.daemon, self.library, self.plist_library = map(Path, (daemon, library, plist_library))
        self.pairing, self.logs = Path(pairing), Path(logs)
        self.child = None
        self.directory = None
        self.log = None

    def start(self):
        for path in (self.daemon, self.library, self.plist_library):
            if not path.is_absolute() or not path.is_file():
                raise InstallError("Guest app services are missing from this build: " + str(path))
        for path in (self.pairing, self.logs):
            self._prepare_data_directory(path)
        self.directory = Path(tempfile.mkdtemp(prefix="lighttouch-usb-"))
        self.socket = self.directory / "mux.sock"
        self.endpoint = self.directory / "qemu.sock"
        env = dict(os.environ, USBMUXD_QEMU_ADDR=str(self.endpoint))
        self.log = (self.logs / (self.directory.name + ".log")).open("xb")
        os.chmod(self.log.name, 0o600)
        try:
            self.child = subprocess.Popen([str(self.daemon), "-f", "-S", str(self.socket), "-C", str(self.pairing),
                                           "-P", str(self.directory / "daemon.pid")],
                                          stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT,
                                          env=env, close_fds=True, start_new_session=True)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if self.child.poll() is not None:
                    raise InstallError("Guest USB bridge exited. Inspect " + self.log.name)
                if self._private_socket(self.socket) and self._private_socket(self.endpoint):
                    return dict(os.environ, IT_USB_TCP=str(self.endpoint))
                time.sleep(.025)
            raise InstallError("Guest USB bridge did not create its private listeners within 5 seconds.")
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _prepare_data_directory(path):
        """Migrate only the exact owned folder, traversing without symlinks.

        Existing ancestors are never chmodded. Open directory FDs pin the
        ownership/mode checks and fchmod to the same object, not a replaced path.
        """
        path = Path(path)
        if not path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
            raise InstallError("Guest service data folder {}: choose an absolute directory without '..'.".format(path))
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        parent_fd = None
        target_fd = None
        traversed = Path(path.anchor)

        def open_component(parent, name, component):
            try:
                try:
                    return os.open(name, flags, dir_fd=parent)
                except FileNotFoundError:
                    try:
                        os.mkdir(name, mode=0o700, dir_fd=parent)
                    except FileExistsError:
                        pass  # Re-open with O_NOFOLLOW and check the actual object.
                    return os.open(name, flags, dir_fd=parent)
            except OSError as error:
                reason = error.strerror or str(error)
                try:
                    entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    if stat.S_ISLNK(entry.st_mode):
                        reason = "is a symbolic link; choose its real directory instead"
                    elif not stat.S_ISDIR(entry.st_mode):
                        reason = "is not a directory"
                except OSError:
                    pass
                raise InstallError("Guest service data folder {}: {}: {}.".format(path, component, reason)) from error

        try:
            parent_fd = os.open(path.anchor, flags)
            for name in path.parts[1:-1]:
                traversed = traversed / name
                next_fd = open_component(parent_fd, name, traversed)
                os.close(parent_fd)
                parent_fd = next_fd
            target_fd = open_component(parent_fd, path.name, path)
            value = os.fstat(target_fd)
            if not stat.S_ISDIR(value.st_mode):
                raise InstallError("Guest service data folder {}: is not a directory.".format(path))
            if value.st_uid != os.geteuid():
                raise InstallError("Guest service data folder {}: owned by uid {}, expected uid {}; choose a folder owned by this user.".format(
                    path, value.st_uid, os.geteuid()))
            if stat.S_IMODE(value.st_mode) != 0o700:
                os.fchmod(target_fd, 0o700)
            checked = os.fstat(target_fd)
            entry = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            if (not stat.S_ISDIR(entry.st_mode) or (entry.st_dev, entry.st_ino) != (checked.st_dev, checked.st_ino) or
                    checked.st_uid != os.geteuid() or stat.S_IMODE(checked.st_mode) != 0o700):
                raise InstallError("Guest service data folder {}: directory changed during setup; no bridge was started.".format(path))
        except OSError as error:
            raise InstallError("Guest service data folder {}: {}. Check ownership and access permissions.".format(
                path, error.strerror or str(error))) from error
        finally:
            if target_fd is not None:
                os.close(target_fd)
            if parent_fd is not None:
                os.close(parent_fd)

    @staticmethod
    def _private_socket(path):
        try:
            value = path.lstat()
            return stat.S_ISSOCK(value.st_mode) and value.st_uid == os.geteuid() and not value.st_mode & 0o077
        except FileNotFoundError:
            return False

    @property
    def alive(self):
        return self.child is not None and self.child.poll() is None

    def command(self, action, scratch, ipa=None):
        if not self.alive or not self._private_socket(self.socket):
            raise InstallError("The private guest bridge is not running. Start a fresh iOS session.")
        command = [sys.executable, "-m", "app_services.helper", action, "--socket", str(self.socket),
                   "--library", str(self.library), "--plist-library", str(self.plist_library),
                   "--scratch", str(scratch)]
        if ipa is not None:
            command += ["--ipa", str(ipa)]
        return command

    def close(self):
        # Only our Popen child is signalled; system usbmuxd/phones are never touched.
        if self.child and self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=3)
        if self.log:
            self.log.close()
        if self.directory:
            # Keep unexpected files as evidence; never recursively remove an arbitrary path.
            for name in ("mux.sock", "qemu.sock", "daemon.pid"):
                (self.directory / name).unlink(missing_ok=True)
            try:
                self.directory.rmdir()
            except OSError:
                pass


class ServiceJob:
    """One finite subprocess; no blocking library thread can hang the launcher."""
    def __init__(self, command, timeout=900, owns_install=False, log_path=None):
        env = dict(os.environ)
        # Bundle source root; do not use caller's cwd or global installed modules.
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        self.log = None
        if log_path:
            path = Path(log_path)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.log = os.fdopen(os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb")
        try:
            self.child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, env=env, close_fds=True, start_new_session=True)
        except BaseException:
            if self.log:
                self.log.close()
            raise
        self.events = queue.Queue(maxsize=256)
        self.deadline = time.monotonic() + timeout
        self.done = threading.Event()
        self.result = None
        self.error = None
        # Conservatively own install from helper launch, before its first possible
        # mutation. An asynchronously delivered callback cannot close a Stop race.
        self.mutating = owns_install
        self.target = None
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        retained = 0
        try:
            for raw in iter(lambda: self.child.stdout.readline(65536), b""):
                if self.log and retained + len(raw) <= 8 * 1024**2:
                    self.log.write(raw)
                    self.log.flush()
                    retained += len(raw)
                try:
                    value = json.loads(raw)
                    if not isinstance(value, dict):
                        continue
                except (ValueError, UnicodeError):
                    continue
                if "result" in value:
                    self.result = value["result"]
                if "target" in value:
                    self.target = value["target"]
                if "error" in value:
                    self.error = value["error"]
                if value.get("phase") == "mutation":
                    self.mutating = True
                try:
                    self.events.put_nowait(value)
                except queue.Full:
                    pass
        finally:
            self.child.stdout.close()
            if self.log:
                self.log.flush()
                self.log.close()
            self.done.set()

    @property
    def active(self):
        if self.child.poll() is None and time.monotonic() >= self.deadline:
            self.error = "Guest service timed out; its result is unconfirmed. Inspect the guest before retrying."
            self.child.kill()
        return self.child.poll() is None or not self.done.is_set()

    def cancel(self, guest_ended=False):
        if self.child.poll() is None:
            if self.mutating and not guest_ended:
                raise InstallError("The guest is installing. Wait for its result before stopping iOS.")
            self.child.terminate()

    def successful(self):
        return not self.active and self.child.wait() == 0 and self.result is not None and self.error is None

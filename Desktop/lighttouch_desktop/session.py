"""Common fresh-worker session and strict terminal receipts for desktop hosts.

Host adapters supply their literal native argv and secure storage/log opener.
This class owns no guest files, leases, windows or platform signals. It never
marks a session clean from process exit alone or kills a worker on Stop timeout.
"""
from collections import deque
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading

from .storage import DEFAULT_STORAGE


def strict_clean_exit(code, receipt):
    expected = {"status_available": "1", "state": "0", "confirmed": "1", "dirty": "0",
                "worker_reaped": "1", "worker_exit": "0", "worker_disposal_requested": "0", "frontend_return": "0"}
    return code == 0 and receipt is not None and all(receipt.get(k) == v for k, v in expected.items())


class ManagedSession:
    """Fresh native host process per session; Stop uses its owned stdin pipe.

    No /proc dependency, PID-based signal or in-process QEMU initialization.
    Terminal receipts count only after the owned child and its output are reaped.
    """
    def __init__(self, application_log=None, *, storage=None, open_log=None, label="LightTouch shared desktop launcher"):
        self.storage = storage or DEFAULT_STORAGE
        self.open_log = open_log or self.storage.open_log
        self.label = label
        self.child = None
        self.queue = queue.Queue(maxsize=256)
        self.tail = deque(maxlen=100)
        self.reader_done = threading.Event()
        self.log_path = None
        self.previous_logs = deque(maxlen=3)
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
            with self.open_log(self.application_log, exclusive=False) as stream:
                stream.seek(0, os.SEEK_END)
                if stream.tell() < 1024**2:
                    stream.write(line.encode("utf-8"))

    def diagnostic_report(self):
        text = self.label + "\nNative log: " + str(self.log_path) + "\n"
        for path in self.previous_logs:
            text += "\nPrevious setup/session log: " + str(path) + "\n"
            if path.is_file():
                with os.fdopen(self.storage.open_regular(path), "rb") as stream:
                    size = stream.seek(0, os.SEEK_END)
                    stream.seek(max(0, size - 65536))
                    text += stream.read(65536).decode("utf-8", "replace")
        if self.log_path and self.log_path.is_file():
            with os.fdopen(self.storage.open_regular(self.log_path), "rb") as stream:
                text += stream.read(65536).decode("utf-8", "replace")
                size = stream.seek(0, os.SEEK_END)
                if size > 65536:
                    stream.seek(max(65536, size - 65536))
                    text += "\n--- Recent output ---\n" + stream.read(65536).decode("utf-8", "replace")
        return text + "\n" + "".join(self.errors)

    def start(self, command, log_path, env=None):
        if self.active:
            raise RuntimeError("Stop the current device before starting another session.")
        if self.log_path:
            self.previous_logs.append(self.log_path)
        self.log_path = Path(log_path)
        log = self.open_log(self.log_path, exclusive=True)
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


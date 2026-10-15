"""One owned SDL frontend per session. Never loads or reinitializes QEMU in the GUI."""
from collections import deque
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import threading
import time

MAX_LOG_BYTES = 64 * 1024 * 1024


def fields(line):
    return dict(re.findall(r"\b([a-zA-Z_][a-zA-Z_0-9]*)=([^\s]+)", line))


def process(pid):
    try:
        root = Path("/proc") / str(pid)
        data = (root / "stat").read_text().rpartition(")")[2].split()
        return {"pid": int(pid), "start": data[19], "ppid": int(data[1]),
                "ticks": int(data[11]) + int(data[12]), "uid": root.stat().st_uid}
    except (OSError, ValueError, IndexError):
        return None


def clean_exit(returncode, receipt):
    return (returncode == 0 and receipt is not None and
            all(receipt.get(k) == v for k, v in {"status_available": "1", "state": "0", "confirmed": "1",
                "dirty": "0", "worker_reaped": "1", "worker_exit": "0", "worker_disposal_requested": "0",
                "frontend_return": "0"}.items()))


class Session:
    def __init__(self):
        self.child = None
        self.thread = None
        self.queue = queue.Queue(maxsize=256)
        self.tail = deque(maxlen=100)
        self.final = None
        self.status = {}
        self.identity = None
        self.frontend_identity = None
        self.previous_sample = None
        self.previous_host_sample = None
        self.stopping = False
        self.log_path = None
        self.returncode = None
        self.reader_done = threading.Event()

    @property
    def active(self):
        return self.child is not None and (self.child.poll() is None or not self.reader_done.is_set())

    def start(self, command, log_path, env=None):
        if self.active:
            raise RuntimeError("A guest is already active. Stop it before starting another.")
        self.queue = queue.Queue(maxsize=256)
        self.tail.clear()
        self.final = None
        self.status = {}
        self.identity = None
        self.previous_sample = None
        self.previous_host_sample = None
        self.stopping = False
        self.returncode = None
        self.reader_done.clear()
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(str(self.log_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        log = os.fdopen(fd, "wb")
        try:
            self.child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, start_new_session=True, close_fds=True, env=env)
        except Exception:
            log.close()
            self.reader_done.set()
            raise
        self.frontend_identity = process(self.child.pid)
        self.thread = threading.Thread(target=self._read, args=(self.child, log), daemon=True)
        self.thread.start()

    def _read(self, child, log):
        size = 0
        truncated = False
        try:
            while True:
                raw = child.stdout.readline(65536)
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip()
                critical = line.startswith(("FINAL ", "LightTouch:", "admission worker_pid="))
                if size + len(raw) <= MAX_LOG_BYTES or critical:
                    log.write(raw)
                    size += len(raw)
                elif not truncated:
                    log.write(b"\nLauncher log size limit reached; final receipt is still retained.\n")
                    truncated = True
                # Bounded display backlog. Retain latest status and FINAL even when GUI is busy.
                try:
                    self.queue.put_nowait(line)
                except queue.Full:
                    try:
                        self.queue.get_nowait()
                    except queue.Empty:
                        pass
                    self.queue.put_nowait(line)
        finally:
            child.stdout.close()
            log.flush()
            log.close()
            self.reader_done.set()

    def poll(self):
        lines = []
        while True:
            try:
                line = self.queue.get_nowait()
            except queue.Empty:
                break
            self.tail.append(line)
            lines.append(line)
            if line.startswith("admission worker_pid="):
                values = fields(line)
                candidate = process(values.get("worker_pid", ""))
                if (candidate and candidate["start"] == values.get("start_ticks") and
                        candidate["ppid"] == self.child.pid and candidate["uid"] == os.geteuid()):
                    self.identity = candidate
            elif line.startswith("status "):
                self.status = fields(line)
            elif line.startswith("FINAL "):
                self.final = fields(line)
        if self.child and not self.active:
            self.returncode = self.child.wait()
        return lines

    def stop(self):
        if self.child is None or self.child.poll() is not None or self.stopping:
            return
        current = process(self.child.pid)
        old = self.frontend_identity
        if not current or not old or current["start"] != old["start"] or current["uid"] != old["uid"]:
            raise RuntimeError("Frontend identity changed; no signal was sent. Inspect the retained log.")
        # Use a pidfd where available so a concurrently exiting/reused PID cannot receive SIGINT.
        fd = os.pidfd_open(self.child.pid)
        try:
            again = process(self.child.pid)
            if not again or again["start"] != old["start"]:
                raise RuntimeError("Frontend exited before Stop; no signal was sent.")
            signal.pidfd_send_signal(fd, signal.SIGINT)
            self.stopping = True
        finally:
            os.close(fd)

    def metrics(self):
        host = {}
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, value = line.split(":", 1)
                if key in ("MemTotal", "MemAvailable"):
                    host[key] = int(value.split()[0]) * 1024
        except (OSError, ValueError):
            pass
        host["cpu_percent"] = None
        try:
            values = [int(v) for v in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]
            total, idle = sum(values), values[3] + values[4]
            if self.previous_host_sample:
                old_total, old_idle = self.previous_host_sample
                if total > old_total and idle >= old_idle:
                    host["cpu_percent"] = max(0.0, min(100.0, 100 * (1 - (idle - old_idle) / (total - old_total))))
            self.previous_host_sample = (total, idle)
        except (OSError, ValueError, IndexError):
            pass
        result = {"host": host, "worker": None}
        if not self.identity:
            return result
        current = process(self.identity["pid"])
        if (not current or current["start"] != self.identity["start"] or
                current["uid"] != self.identity["uid"]):
            self.previous_sample = None
            return result
        now = time.monotonic()
        worker = {"cpu_percent": None, "cpu_seconds": current["ticks"] / os.sysconf("SC_CLK_TCK"), "pss_bytes": None}
        if self.previous_sample:
            old_time, old_ticks = self.previous_sample
            if now > old_time and current["ticks"] >= old_ticks:
                worker["cpu_percent"] = 100 * (current["ticks"] - old_ticks) / os.sysconf("SC_CLK_TCK") / (now - old_time)
        self.previous_sample = (now, current["ticks"])
        try:
            for line in (Path("/proc") / str(current["pid"]) / "smaps_rollup").read_text().splitlines():
                if line.startswith("Pss:"):
                    worker["pss_bytes"] = int(line.split()[1]) * 1024
            again = process(current["pid"])
            if not again or again["start"] != self.identity["start"]:
                return result
        except (OSError, ValueError):
            pass
        result["worker"] = worker
        return result

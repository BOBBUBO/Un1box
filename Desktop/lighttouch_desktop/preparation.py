"""Cancellable, bounded-memory archive inspection off the Tk event thread.

The calculated digest detects a change during native import. It is not a claim
of publisher authenticity. Native extraction and per-asset checks remain required.
"""
import hashlib
import json
import os
from pathlib import Path
import queue
import stat
import threading

from .profiles import SetupError


class Preparation:
    def __init__(self, source):
        self.source = Path(source)
        self.cancelled = threading.Event()
        self.events = queue.Queue(maxsize=4)
        self.result = None
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    @property
    def active(self):
        return self.thread.is_alive()

    def start(self):
        self.thread.start()

    def update(self, done, total):
        try:
            self.events.put_nowait((done, total))
        except queue.Full:
            pass

    def _run(self):
        try:
            fd = os.open(str(self.source), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            with os.fdopen(fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise SetupError("Choose an iOS system file ending in .itbase.")
                header = stream.read(12)
                if len(header) != 12 or header[:8] != b"ITPACK01":
                    raise SetupError("This is not an iOS system archive (.itbase). Extract the download ZIP first.")
                size = int.from_bytes(header[8:], "little")
                if not 0 < size <= 64 * 1024**2:
                    raise SetupError("The iOS system archive has an invalid file list.")
                index = stream.read(size)
                if len(index) != size:
                    raise SetupError("The iOS system archive is incomplete. Extract it again from the download ZIP.")
                entries = json.loads(index)["entries"]
                if not isinstance(entries, list) or not entries or len(entries) > 1000000:
                    raise SetupError("The iOS system archive has an invalid file list.")
                expanded = 0
                for entry in entries:
                    n = entry["size"]
                    if type(n) is not int or n < 0 or expanded + n > 64 * 1024**3:
                        raise SetupError("The iOS system archive exceeds the supported size.")
                    expanded += n
                digest = hashlib.sha256()
                stream.seek(0)
                done = 0
                while True:
                    if self.cancelled.is_set():
                        raise SetupError("Setup cancelled. Your original files are unchanged.")
                    block = stream.read(1024**2)
                    if not block:
                        break
                    digest.update(block)
                    done += len(block)
                    self.update(done, before.st_size)
                after = os.fstat(stream.fileno())
                signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                if signature(before) != signature(after) or signature(after) != signature(self.source.stat()):
                    raise SetupError("The iOS system file changed during setup. Choose it again.")
                if self.cancelled.is_set():
                    raise SetupError("Setup cancelled. Your original files are unchanged.")
                self.result = {"sha256": digest.hexdigest(), "expanded_bytes": expanded}
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.error = str(error)

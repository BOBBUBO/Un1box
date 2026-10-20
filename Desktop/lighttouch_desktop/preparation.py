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
from .storage import DEFAULT_STORAGE


class Preparation:
    def __init__(self, source, *, storage=None):
        self.source = Path(source)
        self.storage = storage or DEFAULT_STORAGE
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
            # Retain recent progress if the UI is slower than the reader.
            try:
                self.events.get_nowait()
            except queue.Empty:
                pass
            self.events.put_nowait((done, total))

    def _run(self):
        from .prepared_import import packed_folder
        if packed_folder(self.source):
            return self._run_folder()
        try:
            fd = self.storage.open_regular(self.source)
            with os.fdopen(fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise SetupError("Choose an iOS system file ending in .itbase.")
                signature = self.storage.file_signature(stream.fileno())
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
                after = self.storage.file_signature(stream.fileno())
                if signature != after or after != self.storage.path_signature(self.source):
                    raise SetupError("The iOS system file changed during setup. Choose it again.")
                if self.cancelled.is_set():
                    raise SetupError("Setup cancelled. Your original files are unchanged.")
                self.result = {"sha256": digest.hexdigest(), "expanded_bytes": expanded}
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.error = str(error)

    def _run_folder(self):
        # A packed folder is checked chunk by chunk as it unpacks; here only its index is pinned and sized.
        try:
            fd = self.storage.open_regular(self.source / "index.json")
            with os.fdopen(fd, "rb") as stream:
                index = stream.read(64 * 1024**2 + 1)
            if len(index) > 64 * 1024**2:
                raise SetupError("The prepared folder has an invalid file list.")
            listing = json.loads(index)
            if listing.get("format") != 2:
                raise SetupError("This folder is not a prepared iOS system from Light Touch for Mac.")
            # Unwritten NAND pages stay holes: count the whole files and the stored chunks.
            chunk = listing["chunkBytes"]
            expanded = sum(entry["size"] for entry in listing["files"]) + sum(
                min(chunk, image["size"] - int(number) * chunk) for image in listing["images"] for number in image["chunks"])
            if type(expanded) is not int or expanded > 64 * 1024**3:
                raise SetupError("The prepared folder exceeds the supported size.")
            self.update(1, 1)
            self.result = {"sha256": hashlib.sha256(index).hexdigest(), "expanded_bytes": expanded}
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            self.error = str(error)

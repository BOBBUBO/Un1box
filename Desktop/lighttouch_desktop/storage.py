"""Narrow file/durability operations for the shared desktop domain.

POSIX defaults preserve Linux/macOS behavior. Windows supplies a native backend
that rejects reparse-point paths and publishes with durable Win32 replacement;
missing POSIX flags must never silently turn secure opens into ordinary opens.
"""
import errno
import os
from pathlib import Path
import stat
import tempfile


class StorageOps:
    def _posix(self):
        if os.name == "nt":
            raise OSError(errno.ENOTSUP, "The Windows host must supply native secure file and durability operations.")

    def ensure_directory(self, path):
        self._posix()
        path = Path(path)
        if path.is_symlink():
            raise OSError(errno.ELOOP, "A private output directory cannot be a symbolic link.")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)

    def open_regular(self, path, flags=os.O_RDONLY, mode=0o600):
        """Return an owned noninheritable descriptor; reject links/special files.

        Windows backends provide the same contract via CreateFile/no-reparse
        admission. Writable logs must also have exactly one hard link.
        """
        self._posix()
        fd = os.open(str(path), flags | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, mode)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise OSError(errno.EINVAL, "Expected a regular file.")
            if flags & (os.O_WRONLY | os.O_RDWR) and info.st_nlink != 1:
                raise OSError(errno.EMLINK, "A writable log cannot have multiple hard links.")
        except BaseException:
            os.close(fd)
            raise
        return fd

    def create_temp(self, directory):
        self._posix()
        return tempfile.mkstemp(prefix=".lighttouch-", dir=str(directory))

    def file_signature(self, fd):
        """Stable identity and mutation metadata, using one host API family.

        Compare only with other file_signature results. In particular, Windows
        CRT fstat and path stat do not supply equivalent file identifiers.
        """
        self._posix()
        info = os.fstat(fd)
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def path_signature(self, path):
        fd = self.open_regular(path)
        try:
            return self.file_signature(fd)
        finally:
            os.close(fd)

    def flush_file(self, fd):
        os.fsync(fd)

    def replace_and_sync(self, source, destination):
        """Publish an owned temp and durably flush its containing directory.

        Windows must override this operation, e.g. an audited write-through
        replacement plus required handle flushing; skipping directory fsync
        without supplying Windows durability is not an equivalent backend.
        """
        self._posix()
        os.replace(source, destination)
        directory = os.open(str(Path(destination).parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def immutable_base(self, path, attributes):
        self._posix()
        return not attributes.st_mode & 0o222 or bool(getattr(attributes, "st_flags", 0) & stat.UF_IMMUTABLE)

    def open_log(self, path, *, exclusive):
        self.ensure_directory(Path(path).parent)
        flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_APPEND)
        return os.fdopen(self.open_regular(path, flags), "wb" if exclusive else "ab")


DEFAULT_STORAGE = StorageOps()

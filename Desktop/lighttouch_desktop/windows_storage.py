"""Win32 filesystem backend for the maintained desktop domain.

Native Windows execution is not qualified by the Linux policy tests. Files are
opened without following reparse points, private containers carry a protected
DACL and metadata publication uses native write-through replacement. This is a
filesystem adapter, not complete firmware or worker asset admission.
"""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes as wt
import ntpath
import os
from pathlib import Path, PureWindowsPath
import secrets

from .storage import StorageOps

# Trusted principals may administer this local user independently of the app.
SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
SE_DACL_PROTECTED = 0x1000


def checked_path(value):
    """Reject Win32 alternate namespaces/streams and ambiguous components."""
    value = os.fspath(value)
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("Choose a valid absolute Windows path.")
    if value.replace("/", "\\").startswith(("\\\\?\\", "\\\\.\\")):
        raise ValueError("Windows device namespace paths are unsupported.")
    path = PureWindowsPath(value)
    if not path.is_absolute():
        raise ValueError("Choose an absolute Windows path.")
    raw = value.replace("/", "\\")
    drive, tail = ntpath.splitdrive(raw)
    parts = tail.split("\\")
    reserved = {"CON", "PRN", "AUX", "NUL", *("COM"+str(i) for i in range(1, 10)), *("LPT"+str(i) for i in range(1, 10))}
    for part in parts:
        if not part:
            continue
        if part in (".", "..") or part.endswith((" ", ".")) or any(c in part for c in ':<>"|?*') or any(ord(c) < 32 for c in part):
            raise ValueError("Windows paths cannot contain traversal, alternate streams or ambiguous names.")
        if part.split(".", 1)[0].upper() in reserved:
            raise ValueError("Windows reserved device names cannot be used as files.")
    if not drive:
        raise ValueError("Choose an absolute Windows path.")
    return str(path)


def private_acl(owner, control, entries, user_sid):
    """Accept only a protected, current-user-owned, explicit trusted DACL.

    Unknown/object/callback ACEs fail closed. Windows resolves current-user
    token/SID and the actual on-handle ACL; callers never pass a UI claim here.
    """
    if owner != user_sid or not control & SE_DACL_PROTECTED or not entries:
        return False
    trusted = {user_sid, SYSTEM_SID, ADMINISTRATORS_SID}
    # 0=ACCESS_ALLOWED_ACE; 1=ACCESS_DENIED_ACE. Preserve deny entries, but do
    # not attempt to interpret compound/callback/object ACE representations.
    if any(kind not in (0, 1) or sid not in trusted for kind, sid in entries):
        return False
    return any(kind == 0 and sid == user_sid for kind, sid in entries)


class _FileInfo(ctypes.Structure):
    _fields_ = [("attributes", wt.DWORD), ("creation", wt.FILETIME), ("access", wt.FILETIME),
                ("write", wt.FILETIME), ("volume", wt.DWORD), ("size_high", wt.DWORD),
                ("size_low", wt.DWORD), ("links", wt.DWORD), ("index_high", wt.DWORD), ("index_low", wt.DWORD)]


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [("length", wt.DWORD), ("descriptor", wt.LPVOID), ("inherit", wt.BOOL)]


class _AclSize(ctypes.Structure):
    _fields_ = [("count", wt.DWORD), ("used", wt.DWORD), ("free", wt.DWORD)]


class _Api:
    def __init__(self):
        if os.name != "nt":
            raise OSError("The Windows filesystem backend requires Windows.")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.security = ctypes.WinDLL("advapi32", use_last_error=True)
        def bind(lib, name, result, *arguments):
            fn = getattr(lib, name); fn.restype = result; fn.argtypes = arguments
            return fn
        pointer = ctypes.POINTER
        self.open = bind(self.kernel, "CreateFileW", wt.HANDLE, wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID, wt.DWORD, wt.DWORD, wt.HANDLE)
        self.close = bind(self.kernel, "CloseHandle", wt.BOOL, wt.HANDLE)
        self.info = bind(self.kernel, "GetFileInformationByHandle", wt.BOOL, wt.HANDLE, pointer(_FileInfo))
        self.kind = bind(self.kernel, "GetFileType", wt.DWORD, wt.HANDLE)
        self.mkdir = bind(self.kernel, "CreateDirectoryW", wt.BOOL, wt.LPCWSTR, pointer(_SecurityAttributes))
        self.flush = bind(self.kernel, "FlushFileBuffers", wt.BOOL, wt.HANDLE)
        self.move = bind(self.kernel, "MoveFileExW", wt.BOOL, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD)
        self.drive = bind(self.kernel, "GetDriveTypeW", wt.UINT, wt.LPCWSTR)
        self.free = bind(self.kernel, "LocalFree", wt.LPVOID, wt.LPVOID)
        self.process = bind(self.kernel, "GetCurrentProcess", wt.HANDLE)
        self.open_token = bind(self.security, "OpenProcessToken", wt.BOOL, wt.HANDLE, wt.DWORD, pointer(wt.HANDLE))
        self.token_info = bind(self.security, "GetTokenInformation", wt.BOOL, wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD, pointer(wt.DWORD))
        self.sid_text = bind(self.security, "ConvertSidToStringSidW", wt.BOOL, wt.LPVOID, pointer(wt.LPWSTR))
        self.sddl = bind(self.security, "ConvertStringSecurityDescriptorToSecurityDescriptorW", wt.BOOL, wt.LPCWSTR, wt.DWORD, pointer(wt.LPVOID), pointer(wt.DWORD))
        self.get_security = bind(self.security, "GetSecurityInfo", wt.DWORD, wt.HANDLE, ctypes.c_int, wt.DWORD, pointer(wt.LPVOID), pointer(wt.LPVOID), pointer(wt.LPVOID), pointer(wt.LPVOID), pointer(wt.LPVOID))
        self.control = bind(self.security, "GetSecurityDescriptorControl", wt.BOOL, wt.LPVOID, pointer(wt.WORD), pointer(wt.DWORD))
        self.acl_info = bind(self.security, "GetAclInformation", wt.BOOL, wt.LPVOID, wt.LPVOID, wt.DWORD, ctypes.c_int)
        self.ace = bind(self.security, "GetAce", wt.BOOL, wt.LPVOID, wt.DWORD, pointer(wt.LPVOID))
        self.user_sid = self._user_sid()

    def fail(self, message):
        raise ctypes.WinError(ctypes.get_last_error(), message)

    def sid(self, pointer):
        text = wt.LPWSTR()
        if not self.sid_text(pointer, ctypes.byref(text)): self.fail("Cannot inspect Windows file owner.")
        try: return text.value
        finally: self.free(ctypes.cast(text, wt.LPVOID))

    def _user_sid(self):
        token = wt.HANDLE()
        if not self.open_token(self.process(), 8, ctypes.byref(token)): self.fail("Cannot inspect this user's Windows token.")
        try:
            length = wt.DWORD()
            self.token_info(token, 1, None, 0, ctypes.byref(length))
            if not length.value: self.fail("Cannot measure the Windows user token.")
            data = ctypes.create_string_buffer(length.value)
            if not self.token_info(token, 1, data, length.value, ctypes.byref(length)): self.fail("Cannot read this Windows user token.")
            return self.sid(ctypes.cast(data, ctypes.POINTER(wt.LPVOID))[0])
        finally: self.close(token)

    @contextmanager
    def descriptor(self):
        value = wt.LPVOID()
        # OI/CI propagate private permissions to newly created files/folders.
        sddl = "O:"+self.user_sid+"D:P(A;OICI;FA;;;"+self.user_sid+")(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
        if not self.sddl(sddl, 1, ctypes.byref(value), None): self.fail("Cannot prepare private Windows permissions.")
        try: yield _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), value, False)
        finally: self.free(value)

    def require_private(self, handle):
        owner, dacl, descriptor = wt.LPVOID(), wt.LPVOID(), wt.LPVOID()
        result = self.get_security(handle, 1, 1 | 4, ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(descriptor))
        if result: raise ctypes.WinError(result, "Cannot inspect private Windows folder permissions.")
        try:
            control, revision = wt.WORD(), wt.DWORD()
            if not self.control(descriptor, ctypes.byref(control), ctypes.byref(revision)): self.fail("Cannot inspect the folder DACL.")
            entries = []
            if dacl:
                size = _AclSize()
                if not self.acl_info(dacl, ctypes.byref(size), ctypes.sizeof(size), 2): self.fail("Cannot inspect folder access rules.")
                for index in range(size.count):
                    ace = wt.LPVOID()
                    if not self.ace(dacl, index, ctypes.byref(ace)): self.fail("Cannot inspect a folder access rule.")
                    kind = ctypes.cast(ace, ctypes.POINTER(ctypes.c_ubyte))[0]
                    # ACCESS_ALLOWED/DENIED_ACE has header4, mask4, then SID.
                    if kind not in (0, 1): raise OSError("Unsupported Windows folder access rule; files were retained.")
                    entries.append((kind, self.sid(ace.value + 8)))
            if not private_acl(self.sid(owner), control.value, entries, self.user_sid):
                raise OSError("Device data requires a private folder owned by this user. Choose a new LightTouch data folder; existing files were retained.")
        finally: self.free(descriptor)

    def checked_handle(self, path, access, disposition, directory=False, security=None):
        flags = 0x00200000 | (0x02000000 if directory else 0)  # OPEN_REPARSE_POINT, BACKUP_SEMANTICS
        handle = self.open(path, access, 1 | 2, security, disposition, flags, None)
        if handle == ctypes.c_void_p(-1).value: self.fail("Cannot securely open "+path)
        try:
            info = _FileInfo()
            if not self.info(handle, ctypes.byref(info)): self.fail("Cannot inspect "+path)
            if self.kind(handle) != 1 or info.attributes & FILE_ATTRIBUTE_REPARSE_POINT or bool(info.attributes & FILE_ATTRIBUTE_DIRECTORY) != directory:
                raise OSError("Links, junctions and special files are unsupported: "+path)
            return handle, info
        except BaseException:
            self.close(handle); raise


class WindowsStorage(StorageOps):
    def __init__(self):
        self.api = _Api()

    def _local(self, path):
        root = PureWindowsPath(path).anchor
        if root.startswith("\\\\") or self.api.drive(root) != 3:
            raise OSError("Writable LightTouch data requires a local fixed Windows drive.")

    @contextmanager
    def _directories(self, path, create=False, private=False):
        path = PureWindowsPath(checked_path(path)); handles = []
        if create or private: self._local(str(path))
        try:
            current = PureWindowsPath(path.anchor)
            for component in (None, *path.parts[1:]):
                if component is not None: current /= component
                try:
                    handle, info = self.api.checked_handle(str(current), 0x00020000 | 0x80, 3, directory=True)
                except OSError as error:
                    if not create or getattr(error, "winerror", None) not in (2, 3): raise
                    with self.api.descriptor() as descriptor:
                        if not self.api.mkdir(str(current), ctypes.byref(descriptor)): self.api.fail("Cannot create private folder "+str(current))
                    handle, info = self.api.checked_handle(str(current), 0x00020000 | 0x80, 3, directory=True)
                handles.append(handle)
            if private: self.api.require_private(handles[-1])
            yield str(path)
        finally:
            for handle in reversed(handles): self.api.close(handle)

    def ensure_directory(self, path):
        with self._directories(path, create=True, private=True): pass

    def open_regular(self, path, flags=os.O_RDONLY, mode=0o600):
        import msvcrt
        value = checked_path(path); parent = str(PureWindowsPath(value).parent)
        writable = bool(flags & (os.O_WRONLY | os.O_RDWR))
        if flags & (os.O_CREAT | os.O_TRUNC | os.O_APPEND) and not writable: raise ValueError("Creation, append and truncation require a writable file.")
        with self._directories(parent, private=writable):
            access = (0x80000000 if not flags & os.O_WRONLY else 0) | (0x40000000 if writable else 0)
            # OPEN_ALWAYS does not truncate before regular/hardlink validation.
            disposition = 1 if flags & os.O_CREAT and flags & os.O_EXCL else 4 if flags & os.O_CREAT else 3
            access |= 0x00020000 if writable else 0  # READ_CONTROL for on-handle DACL
            with self.api.descriptor() as descriptor:
                handle, info = self.api.checked_handle(value, access, disposition, security=ctypes.byref(descriptor) if writable else None)
            try:
                if writable: self.api.require_private(handle)
                if writable and info.links != 1: raise OSError("Writable files cannot have multiple hard links: "+value)
                fd = msvcrt.open_osfhandle(handle, (flags & (os.O_RDONLY | os.O_WRONLY | os.O_RDWR | os.O_APPEND)) | os.O_BINARY)
            except BaseException:
                self.api.close(handle); raise
            try:
                os.set_inheritable(fd, False)
                if flags & os.O_TRUNC: os.ftruncate(fd, 0)
                return fd
            except BaseException:
                os.close(fd); raise

    def create_temp(self, directory):
        directory = checked_path(directory)
        for attempt in range(32):
            path = str(PureWindowsPath(directory)/(".lighttouch-"+secrets.token_hex(16)))
            try: return self.open_regular(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY), path
            except FileExistsError: continue
        raise OSError("Cannot allocate a fresh private metadata file.")

    def flush_file(self, fd):
        import msvcrt
        if not self.api.flush(msvcrt.get_osfhandle(fd)): self.api.fail("Cannot flush LightTouch metadata.")

    def replace_and_sync(self, source, destination):
        source, destination = checked_path(source), checked_path(destination)
        if PureWindowsPath(source).parent != PureWindowsPath(destination).parent:
            raise OSError("Metadata replacement must stay inside one private folder.")
        with self._directories(str(PureWindowsPath(destination).parent), private=True):
            fd = self.open_regular(source, os.O_RDWR | os.O_BINARY)
            try: self.flush_file(fd)
            finally: os.close(fd)
            # Inspect an existing destination before native atomic replacement.
            try: fd = self.open_regular(destination, os.O_RDWR | os.O_BINARY)
            except FileNotFoundError: pass
            else: os.close(fd)
            if not self.api.move(source, destination, 1 | 8): self.api.fail("Cannot durably replace LightTouch metadata; prior data was retained.")

    def immutable_base(self, path, attributes):
        # Windows directory READONLY is decorative; it does not stop creating
        # or replacing children. Require actual denial of each mutation right.
        # Whole-base hashing/file admission remains the native worker's job.
        value = checked_path(path)
        with self._directories(value):
            for right in (2, 4, 0x40, 0x100):
                try: handle, info = self.api.checked_handle(value, right, 3, directory=True)
                except OSError as error:
                    if getattr(error, "winerror", None) == 5: continue
                    raise
                else:
                    self.api.close(handle)
                    return False
        return True

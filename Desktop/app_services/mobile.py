"""libimobiledevice adapter used ONLY in a bounded, private helper process.

The parent supplies exact bundled library paths and a private usbmux listener.
Never call this module inside the launcher (library environment is global).
"""
import ctypes as C
import json
import os
from pathlib import Path
import plistlib
import threading
import time

from .catalog import Target
from .ipa import InstallError

P = C.c_void_p
PP = C.POINTER(P)
CALLBACK = C.CFUNCTYPE(None, P, P, P)


class Mobile:
    def __init__(self, library, plist_library):
        self.plist = C.CDLL(str(Path(plist_library).resolve()), mode=C.RTLD_GLOBAL)
        self.lib = C.CDLL(str(Path(library).resolve()))
        self.libc = C.CDLL(None)
        self.libc.free.argtypes = [P]
        specs = {
            "idevice_new": [PP, C.c_char_p], "idevice_free": [P],
            "lockdownd_client_new_with_handshake": [P, PP, C.c_char_p],
            "lockdownd_client_free": [P], "lockdownd_get_value": [P, C.c_char_p, C.c_char_p, PP],
            "lockdownd_start_service": [P, C.c_char_p, PP], "lockdownd_service_descriptor_free": [P],
            "afc_client_new": [P, P, PP], "afc_client_free": [P],
            "afc_get_device_info_key": [P, C.c_char_p, PP],
            "afc_make_directory": [P, C.c_char_p], "afc_remove_path": [P, C.c_char_p],
            "afc_file_open": [P, C.c_char_p, C.c_int, C.POINTER(C.c_uint64)],
            "afc_file_write": [P, C.c_uint64, C.c_char_p, C.c_uint32, C.POINTER(C.c_uint32)],
            "afc_file_close": [P, C.c_uint64], "instproxy_client_new": [P, P, PP],
            "instproxy_client_free": [P], "instproxy_install": [P, C.c_char_p, P, CALLBACK, P],
            "instproxy_browse": [P, P, PP],
        }
        for name, args in specs.items():
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = args, C.c_int
        self.plist.plist_from_xml.argtypes = [C.c_char_p, C.c_uint32, PP]
        self.plist.plist_from_xml.restype = None
        self.plist.plist_to_xml.argtypes = [P, PP, C.POINTER(C.c_uint32)]
        self.plist.plist_to_xml.restype = None
        self.plist.plist_free.argtypes = [P]
        self.plist.plist_free.restype = None
        self.device, self.lockdown = P(), P()
        self.check(self.lib.idevice_new(C.byref(self.device), None), "find guest")
        try:
            self.check(self.lib.lockdownd_client_new_with_handshake(self.device, C.byref(self.lockdown),
                                                                  b"LightTouchLinux"), "lockdown handshake")
        except BaseException:
            self.close()
            raise

    @staticmethod
    def check(code, phase):
        if code != 0:
            raise InstallError("{} failed (device error {}). Try again after iOS finishes starting.".format(phase, code))

    def close(self):
        if self.lockdown:
            self.lib.lockdownd_client_free(self.lockdown)
            self.lockdown = P()
        if self.device:
            self.lib.idevice_free(self.device)
            self.device = P()

    def decode(self, value):
        xml, length = P(), C.c_uint32()
        self.plist.plist_to_xml(value, C.byref(xml), C.byref(length))
        if not xml or length.value > 16 * 1024**2:
            if xml:
                self.libc.free(xml)
            raise InstallError("The guest returned invalid service metadata.")
        try:
            return plistlib.loads(C.string_at(xml, length.value))
        finally:
            self.libc.free(xml)

    def encode(self, value):
        xml = plistlib.dumps(value)
        result = P()
        self.plist.plist_from_xml(xml, len(xml), C.byref(result))
        if not result:
            raise InstallError("Could not encode service request.")
        return result

    def value(self, key):
        value = P()
        self.check(self.lib.lockdownd_get_value(self.lockdown, None, key.encode(), C.byref(value)), "read " + key)
        try:
            return self.decode(value)
        finally:
            self.plist.plist_free(value)

    def target(self):
        product, ios = self.value("ProductType"), self.value("ProductVersion")
        # Current baseline coverage only: do not infer an architecture for other boards.
        arch = {"iPod2,1": "armv6", "iPod4,1": "armv7"}.get(product)
        if arch is None:
            raise InstallError("IPA installation currently supports n72/n81 guest contracts only.")
        return Target(product, ios, arch)

    def service(self, name, prefix):
        descriptor, client = P(), P()
        try:
            self.check(self.lib.lockdownd_start_service(self.lockdown, name.encode(), C.byref(descriptor)),
                       "start " + name)
            self.check(getattr(self.lib, prefix + "_client_new")(self.device, descriptor, C.byref(client)),
                       "connect " + name)
            return client
        except BaseException:
            if client:
                getattr(self.lib, prefix + "_client_free")(client)
            raise
        finally:
            if descriptor:
                self.lib.lockdownd_service_descriptor_free(descriptor)

    def install(self, path, bundle_id, emit):
        """Private AFC stage then asynchronous install; Complete AND browse required."""
        import uuid
        afc = self.service("com.apple.afc", "afc")
        remote = ("PublicStaging/lighttouch-" + uuid.uuid4().hex + ".ipa").encode()
        handle = C.c_uint64()
        opened = False
        try:
            free = P()
            self.check(self.lib.afc_get_device_info_key(afc, b"FSFreeBytes", C.byref(free)), "query free space")
            try:
                available = int(C.string_at(free))
            finally:
                self.libc.free(free)
            total = Path(path).stat().st_size
            needed = total * 2 + (16 << 20)
            if available < needed:
                raise InstallError("The guest needs {} MiB free; it has {} MiB.".format(needed >> 20, available >> 20))
            # Existing PublicStaging is expected; mkdir's exists result is harmless.
            self.lib.afc_make_directory(afc, b"PublicStaging")
            self.check(self.lib.afc_file_open(afc, remote, 3, C.byref(handle)), "open staging upload")
            opened = True
            sent = 0
            with open(path, "rb") as reader:
                while True:
                    chunk = reader.read(65536)
                    if not chunk:
                        break
                    offset = 0
                    while offset < len(chunk):
                        written = C.c_uint32()
                        self.check(self.lib.afc_file_write(afc, handle, chunk[offset:], len(chunk) - offset,
                                                           C.byref(written)), "upload IPA")
                        if not 0 < written.value <= len(chunk) - offset:
                            raise InstallError("The guest returned an invalid upload length.")
                        offset += written.value
                        sent += written.value
                    emit({"phase": "upload", "percent": sent * 100 // total})
            self.check(self.lib.afc_file_close(afc, handle), "close staging upload")
            opened = False
            client = self.service("com.apple.mobile.installation_proxy", "instproxy")
            try:
                self._install(client, remote, emit)
            finally:
                # Join the callback updater before releasing its Python callback,
                # then use a fresh service for browse (the old updater may still
                # own its command when Complete first wakes our waiting thread).
                self.lib.instproxy_client_free(client)
                self._callback = None
            client = self.service("com.apple.mobile.installation_proxy", "instproxy")
            try:
                # A callback alone is insufficient: independently query the installed list.
                options, result = self.encode({"ApplicationType": "User"}), P()
                try:
                    self.check(self.lib.instproxy_browse(client, options, C.byref(result)), "verify installed app")
                    apps = self.decode(result)
                    found = next((a for a in apps if a.get("CFBundleIdentifier") == bundle_id), None)
                    if found is None:
                        raise InstallError("Install reported Complete but the app is absent from the guest's app list.")
                    return {"bundle_id": bundle_id, "version": found.get("CFBundleVersion"), "installed": True}
                finally:
                    if result:
                        self.plist.plist_free(result)
                    self.plist.plist_free(options)
            finally:
                self.lib.instproxy_client_free(client)
        finally:
            if opened:
                self.lib.afc_file_close(afc, handle)
            self.lib.afc_remove_path(afc, remote)
            self.lib.afc_client_free(afc)

    def _install(self, client, remote, emit):
        terminal = threading.Event()
        outcome = {"error": None, "last": time.monotonic()}

        @CALLBACK
        def callback(command, status, context):
            try:
                reply = self.decode(status)
                outcome["last"] = time.monotonic()
                if reply.get("Error"):
                    outcome["error"] = str(reply.get("ErrorDescription") or reply["Error"])
                    terminal.set()
                elif reply.get("Status") == "Complete":
                    terminal.set()
                else:
                    emit({"phase": "install", "status": reply.get("Status", "Installing"),
                          "percent": reply.get("PercentComplete")})
            except BaseException as error:
                outcome["error"] = "Invalid install callback: " + str(error)
                terminal.set()

        options = self.encode({})
        self._callback = callback  # Kept until client_free joins the updater thread.
        try:
            emit({"phase": "mutation", "status": "Installing; wait for completion before stopping iOS"})
            self.check(self.lib.instproxy_install(client, remote, options, callback, None), "submit install")
            deadline = time.monotonic() + 600
            while not terminal.wait(.2):
                now = time.monotonic()
                if now >= deadline or now - outcome["last"] > 90:
                    # Freeing the client can join a blocked library thread. Retire this
                    # dedicated process instead, never free callback state under it.
                    emit({"error": "Install timed out; guest result is unconfirmed. Inspect before retrying."})
                    os._exit(2)
            if outcome["error"]:
                raise InstallError(outcome["error"])
        finally:
            self.plist.plist_free(options)

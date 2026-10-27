"""Client-only attachment to a native-owned private guest usbmux endpoint.

This module never starts/stops usbmuxd, unlinks its sockets, opens the system
usbmux endpoint or loads mobile libraries into the launcher. The native
publisher/lifecycle is a separately required implementation and qualification.
"""
import json
import os
from pathlib import Path
import stat
import sys

from app_services.ipa import InstallError
from .profiles import regular

FORMAT = "lighttouch-native-services"
SESSION_ENV = "LIGHTTOUCH_SHARED_SERVICE_SESSION"


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate native services receipt field.")
        value[key] = item
    return value


def socket_identity(value):
    """Accept only a canonical owner-only Unix socket in its private folder."""
    if not isinstance(value, str) or not value.startswith("UNIX:"):
        raise ValueError("A native private Unix client endpoint is required.")
    name = value[5:]
    path = Path(name)
    if (not path.is_absolute() or str(path) != name or ".." in path.parts or
            "\0" in name or len(os.fsencode(name)) >= 104 or path.resolve(strict=True) != path):
        raise ValueError("Native client endpoint is not a canonical local socket path.")
    info, parent = path.lstat(), path.parent.lstat()
    if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or
            not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid() or parent.st_mode & 0o077):
        raise ValueError("Native client endpoint and its folder must be private and owned by this user.")
    return path, (info.st_dev, info.st_ino, parent.st_dev, parent.st_ino)


class NativeServicesState:
    """Receipts bind the saved device, one fresh native owner and this launch."""
    def __init__(self):
        self.reset(None, None)

    def reset(self, launch_id, profile_id):
        self.launch_id, self.profile_id = launch_id, profile_id
        self.owner_pid = None
        self.generation = 0
        self.endpoint = None
        self.endpoint_identity = None
        self.error = None

    def invalidate(self):
        self.endpoint = None
        self.endpoint_identity = None

    def observe(self, line, owner_pid):
        if not line.startswith("SERVICES "):
            return False
        try:
            if len(line.encode("utf-8")) > 8192:
                raise ValueError("Native services receipt exceeds 8 KiB.")
            value = json.loads(line[9:], object_pairs_hook=_unique)
            if not isinstance(value, dict):
                raise ValueError("Native services receipt must be an object.")
            common = {"format", "version", "launch_id", "profile_id", "owner_pid", "generation", "state"}
            fields = common | ({"client_socket"} if value.get("state") == "ready" else set())
            if (set(value) != fields or value.get("format") != FORMAT or type(value.get("version")) is not int or
                    value["version"] != 1 or not self.launch_id or not self.profile_id or
                    value.get("launch_id") != self.launch_id or value.get("profile_id") != self.profile_id or
                    type(value.get("owner_pid")) is not int or owner_pid is None or value["owner_pid"] != owner_pid or
                    type(value.get("generation")) is not int or not self.generation < value["generation"] <= 2**53 or
                    value.get("state") not in ("ready", "unavailable", "retired")):
                raise ValueError("Native services receipt identity, generation or schema is invalid.")
            endpoint = identity = None
            if value["state"] == "ready":
                endpoint, identity = socket_identity(value["client_socket"])
            self.owner_pid = owner_pid
            self.generation = value["generation"]
            self.endpoint, self.endpoint_identity = endpoint, identity
            self.error = None
            return True
        except (ValueError, OSError, TypeError, UnicodeError) as error:
            self.invalidate()
            self.error = str(error)
            return False

    def current_endpoint(self):
        if self.endpoint is None:
            return None
        try:
            path, identity = socket_identity("UNIX:" + str(self.endpoint))
            if identity != self.endpoint_identity:
                raise ValueError("Native client socket was replaced; wait for a fresh native receipt.")
            return path
        except (ValueError, OSError) as error:
            self.invalidate()
            self.error = str(error)
            return None


class NativeClientBridge:
    """Shared Apps/ServiceJob interface; native session remains the sole owner."""
    def __init__(self, session, profile_id, library, plist_library):
        self.session, self.profile_id = session, profile_id
        self.library, self.plist_library = (Path(path).absolute() for path in (library, plist_library))
        for path, label in ((self.library, "bundled mobile-device library"),
                            (self.plist_library, "bundled plist library")):
            regular(path, label)
        self.closed = False

    @property
    def alive(self):
        state, child = self.session.services, self.session.child
        return (not self.closed and child is not None and child.poll() is None and
                not self.session.stopping and state.profile_id == self.profile_id and
                state.owner_pid == child.pid and state.current_endpoint() is not None)

    def command(self, action, scratch, ipa=None):
        if action not in ("info", "install") or (action == "install") != (ipa is not None):
            raise InstallError("Choose a supported guest app operation and IPA.")
        if not self.alive:
            raise InstallError("This native iOS session has not published a current private app-services endpoint.")
        endpoint = self.session.services.current_endpoint()
        if endpoint is None:
            raise InstallError("The native app-services endpoint changed; no operation was started.")
        command = [sys.executable, "-m", "app_services.helper", action, "--socket", str(endpoint),
                   "--library", str(self.library), "--plist-library", str(self.plist_library),
                   "--scratch", str(scratch)]
        if ipa is not None:
            command += ["--ipa", str(ipa)]
        return command

    def close(self):
        # Stop/reap/unlink are exclusively the native owner’s responsibility.
        self.closed = True

"""Windows-only daemon receipt binding; not wired into the shared product yet.

The native parent must own the child handle and private ACL-pinned directories.
It supplies a handle-based live identity callback and actually reaps the child.
This module never launches/signals a PID, probes a port or infers ownership from
an untrusted receipt. Host tests do not qualify the native endpoint boundary.
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

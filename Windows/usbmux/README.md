# Windows guest-service OS adapter (source checkpoint)

The peer/connector C files cross-compile to Win64 objects with GCC13 and
`-Wall -Wextra -Werror` (source b36e542e). No daemon/mobile library has been built
from this adapter, linked or executed on Windows. It is not packaged or enabled
by WindowsHost.
The shared Apps window, catalog and IPA algorithms stay in `Desktop/app_services`.
This directory changes only the native process/socket boundary and validates
readiness against an already owned child. It is not a replacement installer.

## Native source handoff

`SOURCES.json` pins daemon/libusbmuxd preimages and the audited Windows reference,
including its license. Apply `windows-host.patch` to **unmodified** usbmuxd
`5cb90897479b9021a69db9698fea071ef7b2eceb`, and `libusbmuxd-owned.patch` to
unmodified libusbmuxd 2.1.1. Do not layer the reference patch or Linux patches on
top. Retain the pinned native protocol state machines. Build only the QEMU
transport, never the physical USB backend, upstream system main or service units.

For the daemon, compile the translation units named in `SOURCES.json` with
`windows-compat.h` force-included, `WIN32` and `_WIN32` defined, and this directory
on the include path. Add `windows-main.c` and `windows-peer.c`. Link the listed
WinSock/IP Helper/security/Shell libraries and pinned libplist/glue. Their build
configuration and generated headers still need a reviewed finite build packet;
the source list is **not** a successful build recipe.

For libusbmuxd, apply the connector patch and compile `windows-connect.c` and
`windows-peer.c` into that DLL with the stated libraries. Do **not** force-include
the daemon compatibility header into libusbmuxd. The Windows connection branch
always requires the exact admitted `USBMUXD_SOCKET_ADDRESS=127.0.0.1:<port>`,
`LTW_USBMUX_PID` and decimal `LTW_USBMUX_CREATED` FILETIME; missing/malformed values
return an error before connection. It never falls back to localhost:27015.
The existing POSIX branch retains its original behavior.

## Candidate endpoint boundary

The adapter uses native reverse established TCP ownership rows and a held peer
process handle, checks both loopback endpoints and the current-user SID, and
rechecks the owner table/liveness before accepting. The library connector also
requires the exact owned daemon PID and creation time. Ambiguous rows, access
denied, races and unknown identities fail closed before protocol bytes. This is
a **candidate** kernel-ownership boundary, different from the authentication
preface previously proposed in SERVICES.md. Actual Windows foreign-user,
socket-churn, PID-reuse and multi-device tests are mandatory before enabling it.
Same-user processes have the same trust as an owner-only POSIX socket; this does
not authenticate a particular guest or resist a malicious process of that user.

Listeners bind exclusive ephemeral IPv4 loopback ports and cannot be inherited.
The parent must own/pin private pairing/log/scratch folders and pass an inherited
stdin pipe. No PID watcher or abrupt ExitProcess is used. EOF, malformed input,
or `stop\n` cooperatively stop the daemon. Control reads are nonblocking; guest
transfers keep the existing progress deadline while checking Stop at least every
100 ms during socket retry. Main closes listeners, clients and control before
printing its retirement line. The parent must still wait/reap and drain output;
that line alone is never a clean-shutdown proof.

Readiness carries `LTW_SESSION_PROFILE` and `LTW_SESSION_TOKEN` (fresh 32-character
lowercase hex identity tags, **not secrets**) and positive canonical decimal
`LTW_SESSION_GENERATION` (at most 2^53-1). `session.py` binds that line to the
owned process handle's PID/FILETIME and selected saved profile. It invalidates
availability before Stop and when identity/liveness changes. It does not launch
the child, validate ACLs, enable Apps or prove Windows endpoint behavior.

## Next required work

Root must allocate a bounded MinGW build and retain compiler/PE closure/license
evidence. Actual Windows must then verify private-folder pins/ACLs, Unicode
paths, launch/readiness, foreign/malformed endpoints, blocked/NAKing/silent guest
Stop, failed startup, two saved-device isolation and exact process retirement.
The held child process must supply identity directly (GetProcessTimes), never
by reopening a receipt PID. Shared native loader/allocator/private storage hooks
remain the desktop owner's scope. Local IPA/Legacy Store and persistence target
acceptance remain required after transport qualification.

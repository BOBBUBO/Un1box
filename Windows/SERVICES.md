# Shared Windows guest app services: implementation plan

The shared Windows development bundle currently leaves Apps unavailable.
Legacy Store search, local IPA installation and installation persistence remain
required for desktop parity. Merely bundling `Desktop/app_services` does not
qualify them. This plan does not enable transport or claim Windows execution.

## Keep one app-services implementation

`Desktop/app_services` remains responsible for catalog/search, IPA validation,
AFC upload, installation callbacks, independent installed-list verification,
and the Tk Apps window. Add narrow transport, library-loading, private-storage
and owned-process hooks. Do not port the separate Windows reference installer
or create a second catalog/UI implementation.

Before Windows library loading, remove allocator assumptions in the shared
`Mobile` adapter: free libplist XML with its public `plist_mem_free`, and read
AFC free-space data with `afc_get_device_info` plus `afc_dictionary_free`.
These functions free memory in the allocating DLL. Bound and validate the
returned dictionary. Coordinate these shared-file edits with the desktop
owner; the Windows worker owns Windows-specific adapters.

## Existing native reference reduces the porting work

The audited `BerryPVZ/LightTouchWin` reference at
`7aecedc273f66d3a4aeeb27fee750629c6fffc3c` already contains:

- `Windows/usbmux/windows-compat.h`: WinSock error/nonblocking/poll adaptation,
  checked conversion of socket handles to the existing daemon FD type, and
  the guest TCP wire header.
- `Windows/usbmux/windows-host.patch`: OS-boundary changes to the upstream
  daemon's client/device/QEMU transport/utilities, while retaining the native
  usbmux and TUSB protocol state machines.
- `Windows/usbmux/windows-main.c`: a foreground QEMU-only daemon with two
  ephemeral loopback listeners and a private configuration-directory option.
- `Windows/build-mobile-libs.sh`: pinned library/TLS-build reference.

Review and adapt those narrow OS changes against our exact pinned usbmuxd
source, retaining attribution and licenses. Do not wholesale copy the build
scripts or treat existing source as compiled/qualified here. In particular,
the reference's unguarded listener setup, abrupt `ExitProcess` parent watcher,
bare loopback endpoints and separate `device-service.c` installer do not meet
the shared product's lifecycle, endpoint or single-implementation contracts.

## Endpoint and lifecycle contract

Use one session-owned native QEMU-only daemon and its private pairing folder.
Every saved device gets distinct pairing, logs, scratch and endpoint identity,
even when two devices share one immutable base. Never probe physical USB,
attach to the system daemon, use a global pairing directory or require admin
installation.

QEMU already accepts `IT_USB_TCP=127.0.0.1:<port>`. On Windows, libusbmuxd
2.1.1 ignores a `UNIX:` override and can fall back to port 27015. Reject missing
or malformed endpoint configuration before loading it. A Windows adapter must
always supply the exact admitted session endpoint and must never select the
physical/system fallback.

Bare localhost is not an owner-only channel. Before enabling services, select
and verify a Windows endpoint boundary. The concrete candidate is two
exclusive loopback listeners with a fresh per-session authentication preface
on both the libusbmuxd client and QEMU TUSB connections. Retain existing
protocol payloads after authentication; keep the extension opt-in so current
POSIX sockets retain their existing protocol. Generate the secret in private
state, pass it through the owned process environment or protected handles,
and exclude it from command lines, logs, receipts and public artifacts.
Unauthenticated clients must time out without blocking guest progress.
This requires narrow corresponding daemon/libusbmuxd/QEMU connection changes;
it is a proposal, not an implemented transport guarantee.

Publish endpoint availability only after exclusive bind, owned-child PID and
creation identity checks, and readiness. Bind the receipt to launch token,
selected profile and monotonic generation. Invalidate it before Stop and on
daemon/worker exit. Cooperative shutdown must wake blocked polling, close the
owned listeners and await/reap the daemon. A clean FINAL must include that
retirement. Do not signal a reused PID or claim clean shutdown after killing
a live guest. Explicit Windows ACL/path admission remains authoritative for
pairing, logs and temporary IPA data.

## Implementation and verification sequence

1. Add the narrow native daemon OS adapter and exact dependency/source/license
   manifest. Preserve the existing TUSB/usbmux state machines. Prepare one
   bounded cross-build packet; compilation requires a separate Root allocation.
2. Add authenticated endpoint admission, failure/cancellation tests and shared
   allocator/loader/storage hooks with the desktop owner. Test partial and
   wrong authentication, foreign/missing endpoints, timeouts, stale receipts,
   simultaneous saved devices and confirmed process retirement.
3. Stage the new dependencies with a complete PE closure and provenance;
   synthetic tests and Linux cross-compilation remain host evidence only.
4. On actual Windows, prove daemon-only ownership, blocked/unauthenticated
   client handling, private pairing and stop/reap behavior before enabling
   `WindowsHost.start_services`.
5. On the real guest, identify board/iOS, install a local IPA through the shared
   helper, require Complete plus an independent installed-list query, then
   test verified-HTTPS Legacy Store search/install and restart persistence.
   Repeat with two saved devices and retain real target logs/screenshots.

The current shared ZIP is a development bundle for Windows UI/runtime/guest
testing. Its successful static archive audit does not close any of these
remaining app-services gates.

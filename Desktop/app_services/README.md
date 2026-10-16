# Shared desktop app services

`catalog.py` ports the catalog protocol and copy validation from
`Packages/LightTouchCore/Sources/LightTouchCore/Features/CatalogClient.swift`,
`CatalogCopy.swift` and `CatalogFilter.swift`. The existing repository license
applies. It uses Python 3.9+ standard-library HTTPS with certificate verification.

The desktop UI can run `CatalogClient.search(query, target)` on a background
thread, show `incompatibility(app)` on unavailable rows, use `versions(app)` for
other copies, and call `download(app, target, private_scratch)` only after a user
selects a copy. `Target` contains the selected guest's product type, iOS version,
CPU architecture and device family; it describes the guest, not the Linux host.
Every search and copy recheck names the actual target. Downloads revalidate copy
identity, availability, architecture, encryption classification and minimum OS,
then stream to a private file and verify the archive's size/MD5. MD5 is an archive
integrity check, not a publisher signature. A progress callback receives transferred
bytes and expected size; cancellation and failed integrity checks remove the owned
partial file. The caller owns successful scratch files and must retain or remove
them when the queued installation finishes.

The catalog can be tested without downloading applications:

```sh
python3 -m unittest discover -s Linux/tests -p 'test_app_services_catalog.py' -v
```

## Desktop installation

The launcher's **Apps…** window queries the running guest's `ProductType` and
`ProductVersion`, searches Legacy Store with that actual target, and supports
**Download and install** and **Install local IPA…**. Downloads remain private and
are removed after installation or failure. No app is downloaded merely by opening
the window or searching. Open an installed app from its icon in the guest.

`bridge.py` starts the pinned QEMU-only `usbmuxd-qemu` for each fresh native
session. It uses a fresh owner-only Unix socket directory, `-f -S <mux.sock>
-C <device pairing directory> -P <owned pidfile>`, and sets
`USBMUXD_QEMU_ADDR=<qemu.sock>` only in that daemon's environment. The native
session receives `IT_USB_TCP=<qemu.sock>` before its fresh worker starts. The
bridge stays available through native orderly shutdown and is retired only after
the native frontend has exited. Pairing data remains in the device's private
launcher directory. No system usbmux daemon or physical USB device is used.

`helper.py` runs in a finite separate process with explicit library paths and
`USBMUXD_SOCKET_ADDRESS=UNIX:<owned mux.sock>`. It validates socket ownership and
permissions before loading the libraries. It uses public libimobiledevice APIs
for lockdown, AFC, and installation_proxy; no additional Python package is
required. `ipa.py` validates ZIP paths, app metadata, ARM slices, encryption and
minimum OS, snapshots into private scratch, and repairs the main executable's
mode to 0755 in the copy. It never extracts the archive or modifies the source.

The installer checks `2 * IPA bytes + 16 MiB` free through AFC, handles partial
writes, stages a unique PublicStaging name, and cleans up only that owned upload.
Install status comes from installation_proxy callbacks. A Complete callback is
followed by joining the updater and a fresh installed-app list query; both must
succeed before the UI reports installed. The callback watchdog is 90 seconds
idle/600 seconds absolute; the parent helper bound is 900 seconds, including
preflight/upload/connection. The info helper has a 30-second bound. An abnormal
exit or timeout is unconfirmed, never success. Helper JSONL logs (bounded to
8 MiB) and bridge logs are private under the launcher library's `service-logs`.

Install ownership starts before the helper is launched: launcher Stop/Close
wait for the operation, avoiding a race with asynchronous mutation callbacks.
A guest which independently exits causes the helper to be retired and retains an
unconfirmed result. Downloads can be cancelled; once an install helper starts,
wait for its result. Native shutdown's existing 35-second deadline and strict
FINAL receipt remain unchanged. There is no auto-retry of submitted installs,
uninstall, automatic respring or automatic guest launch. Current target contracts
are n72/iPod2,1 and n81/iPod4,1; this does not extend firmware-preparation coverage.

### Bundle contract

Expected paths below the AppDir `usr` directory (or pass launcher overrides
`--usbmuxd`, `--imobile-library`, `--plist-library`):

* `bin/usbmuxd-qemu`: source pin
  `5cb90897479b9021a69db9698fea071ef7b2eceb`, QEMU-only `usb-qemu.c` backend.
* `lib/lighttouch/libimobiledevice-1.0.so.6` (1.4.0) and its closure.
* `lib/lighttouch/libplist-2.0.so.4` (2.7.0) and its closure.
* `Desktop/app_services/` staged as `app_services/` alongside the shared launcher.

Dependency sources/hashes are in `build-support/dependencies.json`: glue 1.3.2,
libusbmuxd 2.1.1, libtatsu 1.0.5, and the existing pinned crypto inputs/patches.
Use a shared-library build with its RPATH/closure audited in the AppImage. The
host's libplist 2.4 does not satisfy the daemon's >=2.6 requirement. Do not swap in
a generic system daemon. If service binaries are absent, normal guest boot stays
available and the launcher explicitly marks Apps unavailable.

Run the synthetic archive/protocol/process tests with:

```sh
python3 -m unittest discover -s Linux/tests -p 'test_app_services*.py' -v
python3 -m unittest discover -s Linux/launcher/tests -v
```

These tests and source plumbing are not actual Linux guest installation/launch
acceptance. A real authorized IPA, guest installation, visible icon/launch, and
strict terminal shutdown still need target qualification. No supplied IPA was
found during this implementation; no arbitrary payload was acquired for a test.
The small typed guest-agent API is not an IPA upload transport. A downloaded file
is not an installed app.

## Shared desktop source

Catalog HTTPS, copy/target checks, IPA validation, bounded service jobs,
installation protocol and the Tk Apps window live only in
`Desktop/app_services/`. Linux's historical package is an import shim; release
staging copies the shared implementation directly, without source-checkout
paths. Linux's proven POSIX transport is retained. Windows still needs native
private endpoint/library/process adapters and macOS needs its matching bridge
packaged and connected. Merely staging these modules does not qualify either
platform or enable an unavailable service. Do not fork catalog/IPA/UI logic to
implement those host adapters.

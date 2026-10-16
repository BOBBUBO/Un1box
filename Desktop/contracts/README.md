# Shared desktop control and configuration contract

These are source/API rules, not a new firmware or on-disk state schema.

## Controls

`Desktop/ui/Commands.hpp` is the authoritative ordered command/shortcut/button
mapping. `ControlBar.hpp` implements the actual common layout, draw and ownership
behavior. Home/Power/volume send balanced edges; multiple mouse/finger/keyboard
owners share one hardware down/up pair. Pause, rotation, host screenshot and
ordinary Stop activate on release inside the same pointer control or a nonrepeat
keyboard keydown. C/focus loss/resize cancels all owned input. Adapters retain
release/cancel handling during pause and stop; controls never become guest touch.
S captures a host image. A guest screenshot requires real simultaneous guest
Home+Power edges. Stop is guest-confirmed shutdown plus worker reaping, never a
force-close shortcut. Gyro is deferred; Linux diagnostic replay keys are not
advertised shared product features.

## Saved records and prepared systems

`Portable/include/lighttouch.h` already defines `lt_record_info` and native
library import/select/open APIs. Existing Linux `devices.json` and exported
`lighttouch-linux-profile` version1 remain readable, with no bulk migration or
second persistent catalog introduced by this foundation. `lighttouch_desktop.library`
provides shared validation rules used by Linux today; native storage/integrity
and leases remain authoritative. Android should consume the same domain rules
through its native library rather than importing Python UI code.

A named record retains its identity, immutable system reference, matching package
and board boot assets, audio preference and separate writable state. Reopening
and selecting a saved record reuses those choices. Rename/settings changes keep
its base/state bindings. Another board/build/base or another writable state needs
a new record. Multiple records may reference one immutable extracted base;
state directories must be mutually disjoint and outside every referenced base.
No switching operation copies, deletes or overwrites an existing guest state.
Switch only after strict shutdown/reaping/lease release; active UI disables it.
The current Linux profile exporter still contains host paths, not a portable
firmware manifest. Remap paths explicitly on import and preserve native pin checks.

Display name + prepared board + iOS version/build from available metadata, with
honest preparation and per-target runtime qualification. Merely seeing lock
metadata means prepared, not integrity-verified or guest-qualified. Unavailable
assets stay visible with an actionable status. Firmware inventory/preparation
manifests belong to the firmware-matrix owner; consume its projection when
available, with per-instance identity/state outside that immutable manifest.
There is no all-versions picker or arbitrary CPU selector: current CPU is TCG;
other choices require implemented and qualified backends.

Firmware projection handoff: `tools/firmware-matrix.py` (separate owner/revision)
provides `entries[]` metadata for device/board/product_type/version/build, boot
strategy/external-ROM and matching package family/hash. Its optional prepared
lock projection exposes immutable-base reference/lock SHA with
`lock_metadata_only`, while target acceptance remains `not_assessed`. This UI
foundation does not import an unintegrated inventory or promote those fields to
qualification. Future adapters should match a prepared record to that projection
without storing writable state/identity or host endpoints inside immutable data.

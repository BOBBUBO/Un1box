# Android display ownership

This fixes a source-permitted ordering bug, not a proven explanation of any
retained guest image. Previously any old View could globally cancel input and
clear a newer View's window. A native arrival counter protected slow conversion
results but did not identify who owned the window.

An explicit Activity foreground transition selects the process-local UI owner.
Only its current `displayView`, with focus and a valid Surface, may acquire a
worker-issued display lease. Created/changed/reconnect callbacks reuse that
lease. An obsolete Activity/View cannot claim a new lease just because its
callback arrives late. A genuinely foregrounded Activity can take a new lease.
The View stores the issuing Binder with the token and always sends owned controls
through that captured worker. A new worker's coincident numeric token does not
validate an old binding.

Native attachment checks the lease under the existing control guard before
conversion and at commit. It also retains arrival ordering for overlapping
conversions from the same owner. Conversion and rejected/retired window reference
release remain outside that guard. A converted reference is adopted exactly once
or released exactly once. Presenter snapshots and the one-post-per-lock rule are
unchanged. No Service lock spans platform conversion or window release.

Detach, contact cancellation, touch admission and background pause validate the
lease inside the native guard. An old View's destroy/focus/resize/touch and an old
Activity's stop cannot affect the new owner through those paths. Local retired
View contact IDs are discarded without issuing an unconditional global cancel.
Reconnect routes through the View's eligibility and stored worker binding.
Activity stop keeps the prior session/status pause condition, then uses the
owner-scoped background operation. Pre-start Activity closure does not mark a
stopped worker paused or create a session error.

The native ledger tracks View-owned **admitted** contacts, not guest consumption.
Routine attach, duplicate callbacks and no-contact resize/detach do not cancel
an unrelated Home/Power pulse. A real held-contact handoff uses the existing full
cancel API; that API also releases hardware buttons. Failures remain actionable
native errors. Explicit user Pause/Stop, direct diagnostic input and worker audio
focus cleanup retain their global semantics. The lease does not reinterpret
shutdown confirmation, guest input application, audio focus policy or pixels.

The phase telemetry from `121936c8` remains in this source. Its stage codes,
timestamps and window presence are diagnostic metadata, never guest acceptance.
Bitmap capture and status remain separate calls without an atomic PNG serial.

The pure JVM production-helper cases cover old-owner retirement/reconnect,
legitimate foreground return and numeric token reuse across distinct workers.
The native production-helper cases cover delayed obsolete conversion, retired
controls, duplicate requests, exact converted-reference release and empty versus
held-contact handoff. The existing synthetic View tests explicitly bind fake
ownership by reflection; they test View ID/resize/rejection behavior without a
real Android Surface or native admission. No production test bypass is added.

Required validation after an allocated build: focused JVM suite, native helper
ASAN/UBSAN/Werror run, both ABI JNI syntax/build and updated synthetic View tests.
Then inspect actual repeated recreation/overlap on target with window/phase
metrics, current copied-versus-producer statuses and retained raw/Surface images.
Source/helper tests do not establish guest rendering or prove an earlier failure
was caused by a late callback. Existing failed receipts remain unchanged.

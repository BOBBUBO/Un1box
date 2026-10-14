# Proposed cancellable prepared-base admission

This is a source-only proposal. No new ABI, deadline change, optimized build or
large-base read has been implemented by this plan. The shared header remains
owned by the integration lane; agree declarations with Android before editing it.

The allocated native n81 probe called production `lt_device_open` once and was
stopped by its 90s watchdog before that call returned. It read 4,616,036,138 bytes
in 90.003s, approximately 48.91 MiB/s, using the existing unoptimized runtime.
Eight NAND stores total 17,716,740,096 bytes. Extrapolation at that observed rate
puts just the full read around 345s; this is an estimate under the recorded host
load, not a performance guarantee. The current Linux parent's 60s HELLO deadline
includes `lt_device_open`, so it cannot accommodate this measured admission.
Neither the probe nor this proposal boots the n81 guest or qualifies preparation.

Preserve every current hash, NAND listing/page-count check, immutable-mode check,
identity/state pin, exclusive lease and separate writable NOR/overlay policy.
No trusted receipt, prior validation cache, partial read or preparation result
substitutes for this admission. Do not copy the 18GB immutable base.

## Additive native observer

Propose these functions, reusing the existing zero-to-continue `lt_progress_fn`:

```c
lt_status lt_device_open_observed(const lt_device_config *, lt_progress_fn,
                                 void *opaque, lt_device **, lt_error *);
lt_status lt_library_device_open_observed(lt_library *, const char *record_id,
                                         uint32_t allow_dirty, lt_progress_fn,
                                         void *opaque, lt_device **, lt_error *);
```

Existing functions retain their ABI and delegate with a null observer. The
observed library route must use the same checked record paths and native device
opening as the direct route. The callback runs synchronously on the opening
thread, receives monotonically increasing hashed asset bytes plus their fixed
planned total, and must not reenter the device/library API. Initial enumeration
can report `(0, 0)` until the byte total is known; that is not byte progress.
Count source-file digest input bytes, not listing-string hashes or bytes copied
into writable state. Check callback cancellation between existing 64KiB hash
chunks and before later state operations. Returning nonzero yields
`LT_CANCELLED`, a null output handle, lease release, and no guest start.

Compute the total with checked additions from the same safely enumerated files
and regular-file descriptors that admission validates. Total calculation must
not turn symlinks, special files, missing outputs or overflow into a successful
progress update. Preserve the exact sorted NAND listing digest and existing
SHA256 algorithm. Bound callbacks and transport separately: cancellation checks
run at chunk boundaries, while frontend progress messages are coalesced to at
most two per second. A terminal success message follows complete validation and
state admission; `done == total` alone does not imply admission completed.

RAII must retain the exclusive lease until cancelled work has actually unwound.
Do not return a live handle or load/start an engine after cancellation. Existing
state pins/writable copies that were already safely published can remain; they
must retain their current validity checks on the next attempt. Never mark an
unstarted admission dirty or clear a previously dirty device. Blocking filesystem
IO may outlast cooperative cancellation, so process disposal remains the bounded
fallback and must be reported separately from a native cancelled result.

## Pre-HELLO frontend phase

Linux needs a typed private admission-progress/error packet before its frame-FD
HELLO. The parent must distinguish admitted byte progress, validation completion,
engine creation and frame handshake. Validate packet kind, size/version, bounded
counters and monotonic byte count; progress carries no caller paths or argv.
The worker checks pre-start cancellation from the existing private control socket
in the native callback. Before HELLO, touch/button/guest commands are rejected;
Cancel/Stop cancel admission without claiming guest shutdown. Android uses the
same native observer with an atomic cancellation token and typed service status;
no callback may block on a mutex needed by the cancellation request.

A proposed large-base policy uses an absolute 900s admission budget and a 30s
no-byte-progress timeout, followed by the existing bounded engine/HELLO phase.
These are hard ceilings, not promises of successful admission. Repeated identical
progress or heartbeat messages cannot extend either deadline. Use fresh host
monotonic time with guarded subtraction, reset the engine-creation deadline only
once after native admission succeeds, and retain a terminal cancellation/failure
receipt on timeout. This should be an explicit bounded policy, not arbitrary
engine argv or an unlimited startup override.

The present SDL window is created after HELLO. Cooperative pre-HELLO cancellation
therefore also needs an actual startup UI or a documented signal/control route;
adding progress packets alone does not make the current GUI Cancel usable.
Retain parent-owned disposal if the worker cannot unwind, without claiming that
`LT_CANCELLED` returned. Do not conflate this budget with 30s guest shutdown
confirmation or 35s host disposal after an already started guest.

## Focused verification before another real read

Use small checked fixtures for cancel after an initial chunk, cancel between
NAND files, corrupted final bytes, byte-count overflow, mode/symlink rejection,
wrong state pin, and exclusive-lease release after actual unwinding. Verify a
null observer leaves existing behavior unchanged and library/direct observers
preserve the same validation. A socket regression should reject stalled or
regressing progress, show that heartbeat-only packets do not extend a deadline,
and exercise cancellation before engine creation without loading QEMU.

Any optimization trial should first record an explicit build type and exact
runtime archive hash, then compare SHA256/validation results against the current
implementation on these fixtures. An allocated measured native admission can
subsequently read the real immutable n81 base once under the new bounded policy.
Do not repeat the full read merely to populate a cache or make the same assertion.

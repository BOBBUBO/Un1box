# First current n81 GUI capture and subsequent input

This plan is not a run allocation. Run01 completed full native admission and clean
Stop on older frozen1ff, but its final native image was black/stale. Raw status had
only 5.305 seconds of fresh/lit/agent1 observations. Manual analysis missed that
window. Capture must run independently of operator/root replies or analysis.

`Main.cpp` binds **S**, not F12, to a local native screenshot without Stop. With
`--trace-io`, each GUI frame copy emits `trace_frame` and flushes stdout. The raw
image is copied before SDL key handling in that loop. The screenshot receipt records
the actual source sequence/time and separate worker diagnostic time. None of those
observations alone identifies Home or another guest scene.

`scripts/watch-ready-captures.py` is a default-plan-only host tool. Its execute mode
requires the exact GUI frontend PID/startticks, UID, stdout file inode, private
DISPLAY, window `_NET_WM_PID` and explicit private `--screenshot` path. It holds a
pidfd, waits for verified HELLO, and tails complete lines every50ms. Incomplete or
oversized lines are retained until complete or conservatively rejected; malformed
status/frame observations invalidate prior readiness. No checksum/NAND/device open,
engine API, compiler, Stop, unlock, button or touch operation is performed by it.

A candidate needs RUNNING, current trace/source frame age<=1s, status age<=750ms,
sequence>1, sleep0/backlight>0 and agent1. At most two captures per second and one
attempt per actual sequence are permitted. The first candidate focuses the exact
validated window once. Later focus changes reject injection instead of reacquiring
focus during an interruption test. The tool invokes a **separate** `xdotool key s`
process with no `--window` and no window stack after checking current focus. This is
XTest; the installed xdotool manpage's SENDEVENT NOTES distinguishes it from the
existing capture helper's explicit-window XSendEvent call. Never label the unchanged
`capture-native-checkpoint.py --window` command as XTest.

Each matching screenshot receipt must arrive within2s. Copy its original BMP to a
new capture directory immediately, retain the exact receipt, and defer PNG conversion
and operator analysis. The watcher must be the sole S/capture owner until it returns;
the live native path is mutable. Check the bounded top-down native BMP layout, actual RGB
excluding unused alpha, source/diagnostic ages, sleep/agent/backlight and sequence.
Black/stale/rejected attempts remain on disk. The watcher exits after the first
qualifying **capture**, at its timeout, or after its bounded attempt count. Its output
always says `scene_or_guest_acceptance=false`; even an exit0 does not prove visible
Home, input, a guest ES2 image or physical audibility. A failure leaves the guest
supervisor responsible for its normal bounded shutdown; this observer never kills
another process or clears dirty state.

Offline tests cover fragmented line delivery, oversized line rejection, stale/future
timestamps, absent HELLO, initial frame1, sleeping/agent0/zero-backlight, malformed
latest status, two-Hz/unique-sequence bounds and alpha-only black BMPs. They make no
X-server, engine or guest calls. Run them separately from target evidence:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 Linux/scripts/test-ready-captures.py
```

After an explicit GUI allocation, use compiled b814 frontend/older1ff or a newly
reviewed producer bound to its exact source/hash. Retain run01 serial/offer receipts
before reusing its clean `state-01`; the new worker necessarily performs full native
admission again. Reuse the immutable base directly with matching armv7 and no ROM,
new offers/endpoints, no full copies, and no implicit recovery. Set a private Xvfb
DISPLAY and `SDL_VIDEODRIVER=x11`; keep the existing surfaceless Mesa/software engine
provider explicit. Set `--no-audio --trace-io --debug-touch-trace --seconds 360` and
`--screenshot /private/run/live.bmp`. The session clock starts after HELLO, not at
launcher start before the potentially250-second sanitizer admission.

The owning supervisor discovers the one window, validates its PID/startticks and
passes it to the watcher. Start the watcher before HELLO so it watches unattended:

```sh
python3 Linux/scripts/watch-ready-captures.py \
  --pid "$FRONTEND_PID" --start-ticks "$START_TICKS" --window "$WINDOW_ID" \
  --log /private/run/session.log --output-dir /private/run/readiness \
  --timeout 1200 --max-captures 12 --execute
```

Convert a retained capture after the watcher copies it, then use `view_image` to
inspect it. Keep a separately timestamped X11 presentation capture if comparing the
SDL display; its timestamp is not a native source-frame sequence. Do not wait for a
root reply before the authorized lane independently inspects/proceeds. If the image
is an unclear boot/transitional scene, retain it and use another current capture;
do not auto-unlock from pixel activity or numeric readiness.

For a sleeping display, perform only a bounded balanced Home H down150ms/up using
the validated, focused window, then capture current pixels. The existing combined
`calibrate-wake-unlock.py` proceeds directly from wake capture to slider; use split
stages so actual colored Lock Screen is inspected **before** slider. Derive the
letterbox from actual guest BMP and measured SDL viewport; (.18,.90)→(.92,.90) is
the verified reference candidate, not proof n81 unlocked. Inspect a current Home
image before any app tap. Never substitute a highlighted icon for the app scene.

New n81 Photos may contain no image. A host S screenshot does not add guest media.
Only after inspected Home and a separate balanced, observed stock Home+Power guest
screenshot may the lane admit a visibly populated Photos grid/viewer. Inspect the
actual icon and viewer fit before F6's simultaneous IDs101/102 replay. Retain paired
before/during/after native/presentation images, source ages, mapped viewport, native
admission/application and fixed wire trace; inspect visible response independently
of queued/applied counters. Back/reopen fit before subsequent gestures if at maximum
zoom. Existing frontend has no live frontmost hotkey: do not fabricate a bundle or
run unattended fixed-time RPCs that could cancel held input on startup rejection.

Keep the same worker for input and normal Stop after current capture. An outer
1340-second observation bound covers admission900 + session360 + parentStop35 with
margin. At the bound, the owner uses its known frontend/worker pidfds and normal
INT/Stop policy, preserving dirty and unresolved leases on forced disposal. Existing
360-second fallback remains independent of watcher/operator progress. No GUI/compiler,
NAND read, media/SDK acquisition, ROM, probe, gyro or n81 preparation is authorized
by this source plan. Actual wake/multitouch/visible ES2 acceptance remains pending.

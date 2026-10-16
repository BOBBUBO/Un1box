# Private actual-guest cache exploration controller

These host scripts prepare and control one allocated Linux n72 guest
exploration. They are diagnostic tools, not a shipping cache feature or a
firmware download/preparation route. Cache stays off. Actual Home/Settings
inspection and a fresh exact-packet Root grant are required; no frame counter
or image heuristic creates a scene pass. Preserve each failed packet/run.

Deploy the five Python sources alongside the privately supplied `PLAN.json`
and artifact/receipt paths. Run `prepare-exploration.py --lock-policy` with the
existing audited `Linux/scripts/lock-continuity.py`. Preparation inventories
the existing immutable base, copies only small writable state using independent
inodes, and records actual executable/dependency pins. It does not start a guest
or GUI. Never commit generated inputs, state, inventories or private captures.

`EXPLORATION-TOOLS.json` binds the resolved absolute Xvfb, xdotool and ffmpeg
executables. The controller and capture helper use these paths and verify their
hashes; service PATH is irrelevant. PNG conversion is a three-second bounded
host operation. Its success says nothing about the image's guest scene.

The first private packet failed because its capture converter used bare
`ffmpeg`, which was missing from systemd's PATH. All actual BMPs were retained;
Home129s expired and ordinary shutdown was clean. The correction keeps that
failure and requires a separately granted successor. It does not turn prior
BMPs or a successful host conversion test into guest acceptance.

Run host contracts with a real local ffmpeg available through the initial PATH,
or set `LT_CACHE_TEST_FFMPEG` to its exact executable path:

```sh
python3 tools/cache-exploration/test-exploration.py -v
```

A private deployed packet also sets `LT_CACHE_TEST_LOCK_POLICY` to the exact
pinned `Linux/scripts/lock-continuity.py` used by that packet.

The converter regression runs the actual executable with PATH nonexistent and
with a conflicting basename. The rest exercises filesystem inventories, PIDFD
retirement, strict clean-stop fields, finite cgroup/grant policy, actual88px
toolbar geometry and retained-image continuity. These are host contracts only.
Read the private packet's `RUN-EXPLORATION.md` for its allocation, invocation,
source manifest, operator capture/inspection steps and independent retirement.

The second actual run produced a personally viewed complete Earth Lock, but
model/tool turnaround outlasted its awake window; fresh sleeping status correctly
blocked slider input. `prepare-exploration.py` can now pin that prior private
capture with `--lock-reference DIRECTORY --model-inspected-reference`, after
actual personal PNG inspection. It binds full BMP/PNG/receipt hashes and the
original fresh awake receipt; the reference may belong to an earlier worker.
It never substitutes that old worker identity for the current live owner.

In a separately granted successor, `exploration_control.py home-unlock
--model-inspected --output lock-unlock01` performs one balanced Home, immediate
bounded current-worker awake/frame wait, capture, fresh awake diagnostics and exact Earth/slider-knob/lock-
symbol comparison, followed by one balanced slider. Input is bounded by eight
seconds and the unchanged original Home129s deadline. Conversion happens after
input; full captures, exact region receipts and failed admissions are retained.
No external model/tool roundtrip occurs inside the awake admission. Personally
inspect the retained post-input PNG before any Home attestation or Settings tap;
anchor matching never recognizes Home or creates guest acceptance. No cache
correctness or speed claim follows from these controller contracts.

After balanced Home the helper polls telemetry for at most2.5seconds, reserving
four seconds before the existing input/Home129s deadline for capture and slider.
It requires a fresh awake RUNNING status and a source frame produced since that
Home input began, including frames produced during the balanced key pulse. It
rechecks current worker identity on each poll and retains every observation.
This wait sends no additional Home, Power or touch input and never changes the
reference pixels, exact anchor policy, personal scene gates or original clocks.
The subsequent actual capture must independently remain fresh/awake and use a
post-Home source frame. Timeout or identity/state failure sends no slider.

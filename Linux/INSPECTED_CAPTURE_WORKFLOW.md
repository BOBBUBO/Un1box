The acceptance operator must actually inspect the native image before declaring
its scene. A frame counter, live guest agent or nonzero RGB bytes cannot establish
Home, a focused editor or a successful gesture. Keep capture bytes and diagnostics
with the same worker PID/startticks and retain failed or sleeping captures.

Arm `scripts/watch-sleep-wake.py` before the first HELLO, alongside the independent
visual deadline watchdog. It watches complete status records for current RUNNING
and Sleep1 only, with live owning PID/window checks in the balanced wake handler.
There is no current or historical guest-agent precondition before hardware wake.
Waiting for agent1 before waking can block the wake itself. The handler waits for
fresh awake/agent1 diagnostics and returns an actual PNG after the balanced Power
press; no counters establish its scene. Await that persistent command and emit
the PNG immediately for actual model inspection. Do not detach the waiter and
spend the interval doing other analysis. A witness error requests ordinary owned
Stop and preserves evidence; it never restarts a clock or guest.

For a previously inspected Lock Screen only, `scripts/inspected-continuity.py`
provides a bounded admission policy for a private controller. The controller keeps
the original inspection timestamp unchanged, validates the owning UID/PID/startticks,
argv, window PID, focus and viewport, and reuses that inspection for at most 60
seconds. It sends one balanced 150ms Power press only if current status says the
display is sleeping. Its capture loop lasts at most 12 seconds. Sleeping captures
remain evidence while the loop waits for actual awake diagnostics.

Before the controller admits its immediate balanced slider, the entire fresh native
BMP must equal the image the operator inspected, including clock pixels. Source
frame age must be at most two seconds, diagnostics age at most one second, agent
status must be alive, backlight positive and the display awake. An awake image with
different bytes rejects admission and requires another actual image inspection.
No scene classifier or new Home/editor label is produced. Use one native screenshot
writer during this check so another observer cannot overwrite the receipt's file.

For the reviewed n72 portrait320x480 route, the private adapter can explicitly
select `scripts/lock-continuity.py` instead of full-BMP equality. It compares exact
raw bytes in three fixed caller-inspected anchors: the Earth wallpaper
`(28,120)-(292,399)`, slider knob `(20,407)-(94,459)` and lock symbol
`(150,1)-(170,18)`. Clock digits and animated slider caption are excluded; all
other pixels outside those declared anchors are also not compared. Retain the
complete original/current images and per-anchor hashes. This is continuity with
an actual prior Lock inspection, not recognition of a new scene or a generic
device/layout detector. Other shapes/formats reject. No tolerances, resized
images or learned pixel masks are used.

During rewake, unmatched transitional anchors remain evidence while the loop
boundedly awaits exact matching anchors at at most two captures per second. It
never admits unmatched Earth, knob or lock pixels; an unrelated/different scene
times out safely. The original60s inspection reuse,12s capture budget, owning
PID/window/viewport checks and fresh source/diagnostic limits remain unchanged.
Neither this Lock-specific mask nor its timestamp semantics apply to Home or
editor labels; their separately inspected continuity gates remain explicit.

Arm observation before this sequence or use the admission captures themselves;
do not wait for an observer, image conversion or another model roundtrip after
admission and before input. Inspect a separate capture after balanced release.
The existing first-HELLO epoch, 129-second Home and 179-second editor watchdog
deadlines, 1340-second outer bound and 35-second shutdown grace stay unchanged.
Only genuine Home/editor observations can authorize Notes typing. A later read
worker requires the visible unique marker, confirmed clean shutdown, actual clean
frontend exit and an available lease.

For Home or a focused Notes editor, invoke `scripts/record-inspected-gate.py`
immediately after actually inspecting that scene. It keeps the honest original
inspection time and scene assertion, obtains a separate fresh native screenshot,
checks full byte equality, current owner/focus/viewport and awake guest status, and
records its verification time separately. It never calls the verification a new
model inspection. Both inspection and verification must precede the original
129/179-second deadline; the original inspection can be reused for at most 60
seconds at admission. Current source age is bounded at two seconds and diagnostics
at one second when verification finishes. Changed pixels reject the gate and stay
as evidence for a new actual inspection. This additive continuity route avoids an
accidental five-second image-to-tool roundtrip requirement; legacy gates keep
their existing validation. A previously admitted gate remains a receipt of that
admission when the guest later intentionally opens another app.

```sh
python3 Linux/scripts/record-inspected-gate.py --run /private/run \
  --capture /private/run/ACTUALLY_VIEWED_CAPTURE --scene home \
  --output home-continuity-01 --model-inspected --execute
```

Use `--scene editor` only after actually viewing the focused new Notes editor.
The command defaults to a plan without `--execute`. It neither starts nor stops
a worker. Its gate is permission for the already scheduled bounded action, not
guest acceptance; actual typed text, durable save and a fresh-worker reread must
still be inspected independently.

Run the target-free policy regression with:

```sh
python3 Linux/scripts/test-inspected-continuity.py
python3 Linux/scripts/test-visual-gates.py
python3 Linux/scripts/test-sleep-wake.py
python3 Linux/scripts/test-lock-continuity.py
```

It exercises the production admission policy with a controlled clock and retained
capture callbacks. Its results qualify host policy behavior only. Actual guest
input, rendering, persistence and audio still need independently inspected target
evidence. The private controller must enforce bounded capture subprocesses and
balanced input; the policy does not operate X11, run an engine or own a worker.

For a personally inspected **blank focused n72 portrait Notes editor**, the
recorder now accepts an explicit `--blank-focused-editor` assertion. This permits
only the two retained, model-vetted caret templates at native rectangle
`[32,104,35,126]`. Header bytes, every alpha byte and every other pixel remain
exact. Unknown caret pixels, a moved caret, clock/body/keyboard changes or another
scene reject. This is not an empty-editor classifier; the caller must actually
inspect that scene. The assertion is retained in both the inspection and current
copy proof. Existing failed attempts are never re-admitted retroactively.

Written and saved marker scenes retain full-image equality. After typing, read
the entire unique body, inspect and tap Done, then capture and inspect the full
body without the caret/keyboard for its written-marker gate. Only afterward use
the inspected Notes Back control and verify the list before the fixed sync. Do
not mask text to accommodate a blinking cursor. Original scene and Stop deadlines
remain unchanged.

The retained-pair host regression takes explicit private BMP inputs; it does not
start a guest or copy assets into the repository:

```sh
python3 Linux/scripts/test-editor-caret.py --original /private/model-inspected-editor.bmp --current /private/model-vetted-current-editor.bmp
```

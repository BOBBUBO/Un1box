The acceptance operator must actually inspect the native image before declaring
its scene. A frame counter, live guest agent or nonzero RGB bytes cannot establish
Home, a focused editor or a successful gesture. Keep capture bytes and diagnostics
with the same worker PID/startticks and retain failed or sleeping captures.

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

Arm observation before this sequence or use the admission captures themselves;
do not wait for an observer, image conversion or another model roundtrip after
admission and before input. Inspect a separate capture after balanced release.
The existing first-HELLO epoch, 129-second Home and 179-second editor watchdog
deadlines, 1340-second outer bound and 35-second shutdown grace stay unchanged.
Only genuine Home/editor observations can authorize Notes typing. A later read
worker requires the visible unique marker, confirmed clean shutdown, actual clean
frontend exit and an available lease.

Run the target-free policy regression with:

```sh
python3 Linux/scripts/test-inspected-continuity.py
```

It exercises the production admission policy with a controlled clock and retained
capture callbacks. Its results qualify host policy behavior only. Actual guest
input, rendering, persistence and audio still need independently inspected target
evidence. The private controller must enforce bounded capture subprocesses and
balanced input; the policy does not operate X11, run an engine or own a worker.

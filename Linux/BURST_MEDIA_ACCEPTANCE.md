# Planned 20-minute stock burst-audio measurement

This plan makes no guest acceptance claim. Root owns any phone/GUI/controller
allocation, actual calibration and final evidence review. The offline reviewer
below only reads existing JSON; it never connects to QMP, starts a worker or
changes guest state. No candidate17 code, SDK, probe or media asset is added.

Apple's original [iPod touch OS3.1 guide](https://cdsassets.apple.com/live/6GJYWVAV/user/ma1196_ipod_touch_3_1_user_guide.pdf)
Clock section, pages110–111, documents the stopwatch's Start/Lap/Stop controls
and a timer with selectable ending sound. Settings pages116/119 describe alerts
volume and automatic lock. This supports UI candidates, not emulator playback.
The source `hw/arm/ipod_touch_i2s.c` describes the stock screenshot sound and
existing LPCM system sounds. Retained Linux run06 proved only bounded stock
shutter/input/render/nonzero-PCM overlap, with physical audibility unavailable.
No Clock alarm/timer or 20-minute workload has been run by this lane.

Before the long measurement, root must inspect actual Home, open Clock using the
current pixels, select Stopwatch and Start, then confirm changing elapsed-time
pixels and source frames. Measure a single balanced stock screenshot chord,
nonzero PCM written/played, current Clock frontmost and unchanged error counters.
A shorter timer with an audible ending sound is an alternative calibration;
ring duration, frame changes and actual playback are unverified. Do not choose a
sleep action, assume a bundle ID or reuse uninspected tap coordinates. Host
keepScreenOn does not disable the guest's own automatic lock. Retain any guest
setting changes in the writable overlay and note whether pulses prevent sleep.

If timer calibration is unavailable, use **periodic stock shutter bursts** while
the stopwatch remains visible. Run the existing `GuestMediaLifecycleTest`
`productionMediaContinuesAfterPauseSurfaceAndAudioFocusChanges` with
`exerciseMedia=true`, `mediaSeconds=1200`, `requireFrameProgress=true`,
`requireGuestAudio=true`, and `exerciseAudioFocus=true`. Its current assertions
compare aggregate deltas; they are not sufficient for sustained activity.
Its result also leaves audiblePlaybackVerified/visibleGuestVerified false.

Root's already authorized private QMP controller may schedule forty fixed chord
attempts at measurement-start +15,+45,…,+1185 host seconds. Keep each pulse in
the middle of a 30-second observation window; it can create a guest Photos file
in retained writable state. Forty requests are not proof of forty saved photos.
Use fixed `input-send-sequence` key events, caller-unique IDs and no arbitrary
monitor commands. Existing n72 keyboard mapping is Cmd+L Power and Cmd+Shift+H
Home. A candidate balanced virtual-time sequence is:

| at-ms | key | down |
| --- | --- | --- |
| 0 | meta_l | true |
| 0 | shift | true |
| 25 | l | true |
| 50 | h | true |
| 400 | h | false |
| 425 | l | false |
| 450 | shift | false |
| 450 | meta_l | false |

This QMP route needs its own actual calibration; the successful shared-API chord
is not automatic QMP qualification. Query `query-input-sequence` for that ID,
allow a bounded host deadline, require completed delivery before another pulse,
and retain every admission/query/completion. Admission alone is not completion,
and completion is not a guest screenshot/audio verdict. On pause, failure or
cleanup explicitly cancel the owned ID with `input-cancel-sequence`. This QMP
sequence is separate from portable native input ownership. Do not rely solely on
Native.cancelInput to cancel its timer. Never press unbalanced Home before Power.

Collect the production one-second `observations.json` and current screenshots
throughout. Per20–30s window require positive produced/presented-frame deltas,
new pulled/written nonzero samples and advancing played frames; retain zero
activity windows rather than hiding them inside a final cumulative total.
Report underruns, short writes, PCM drops, window/native/audio errors, source-frame
age and cadence highwater. Silence between bursts is expected and must be called
burst audio, not continuous audio. A playback-head delta does not prove sound
was heard. Root separately retains exact PID/start ticks, RSS, available thermal
and battery temperatures, concurrent load, actual audibility and visibly changing
Clock captures; missing temperature data stays unavailable, not zero.

Cancel any outstanding QMP sequence before lifecycle transitions. Preserve the
same worker identity and actual labels for pause/resume, Surface loss/return and
audio-focus loss/restoration. The existing test's immediate transition snapshots
do not prove new frames/PCM after each transition. Retain a separate >=30s status
segment after every transition being qualified. After ActivityScenario closes,
root must verify the worker still exists, reopen/explicitly Resume, and collect
another >=30s segment with a new calibrated pulse; test completion may dispose
the target. Reboot/recovery in a different worker is a separate observation.
Do not claim successful same-worker reopening from stale counters or snapshots.

Offline review of retained production-format JSON:

```sh
python3 Linux/scripts/review-burst-media.py \
  --observations /private/media-20min/observations.json \
  --post-observations /private/post-reopen/observations.json \
  --output /private/media-window-review.json
```

Repeat `--post-observations` for separately retained resume/surface/focus segments
in chronological order. Each file needs measurement-start/end labels and the
existing hostElapsedMs/status/observations schema. The reviewer checks bounded
sample gaps, complete counters, monotonicity, unchanged worker identity,
per-window activity/errors and post-segment chronology/counter continuity.
It reports underruns/drops and lifetime cadence without turning them into physical
sound or pixel assertions. Empty post evidence stays unqualified. All guest and
manual acceptance flags remain false even when supplied host checks pass.

BOOTTIME sampling bounds, host CLOCK_MONOTONIC frame times, PCM capture-relative
time and QMP guest virtual milliseconds are separate clocks. Use observation
bounds and recorded conversions; never subtract their raw values indiscriminately.
Finish with balanced controls and confirmed clean Stop, retain the receipt and
worker/state identity, and review any failed/dirty attempt independently.

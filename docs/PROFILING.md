# Recording hitching during play

With a guest running on Android, **Start profile** appears above the FPS graph. Open the iOS app
and scene you want, press Start profile, play through the hitching, then press **Stop profile**.
The label shows the iOS app's localized name. Start again for another scene or game; each recording
gets its own directory. The app name and bundle ID are queried at the start of each recording.
If the guest agent cannot identify the app, Start reports an error and does not begin recording.
Switch apps between captures so each capture's label describes its contents.

Recording continues in fullscreen. Return to the normal view to stop. It also stops when the guest
worker stops. Captures are limited to 15 minutes or about 128 MiB each. Older captures are retained;
there is no automatic deletion. A killed worker may leave a partial capture with its final buffered
events missing; the analysis tool flags a missing stop footer. Pausing/backgrounding the guest while
recording leaves the pause in the elapsed timeline.

## Retrieve and analyze

For a debug APK, with wireless ADB connected and `ANDROID_SERIAL` set:

```sh
tools/perf/profile.py --pull build/profiles
# Choose one of the timestamped directories under build/profiles/profiles/:
/usr/bin/python3 tools/perf/profile.py build/profiles/profiles/CAPTURE \
    --chart build/profile.png --json build/profile.json
```

Pull after Stop so all buffered files have been flushed. This copies only the profiles, not guest
state or firmware. Captures stay in the app's private `files/profiles/` directory on the phone.
The optional chart uses matplotlib; the text and JSON report need only Python's standard library
(Python 3.12+ for safe tar extraction when pulling).

The report provides ceiling, median, low, 1% low and hitches, a frame-time histogram, a timeline,
GL calls ranked by total wall time, and the longest individual engine and Android presentation
intervals. The JSON retains the full frame samples and details of overlapping slow GL calls and
BQL waits/holds for the longest frames. `--top 30` increases the number of detailed frames/calls.

## What is captured

- `metadata.json`: frontmost iOS app response, localized name, bundle ID, start/end timestamps,
  Android model/build, LightTouch version, worker PID and performance environment switches.
- `engine-*-manifest.json`: the APK's build and engine notice/identity manifests.
- `engine-start.log`: the first 64 KiB of engine setup diagnostics, including graphics initialization.
- `engine.log`: up to 8 MiB of engine diagnostics produced during the capture.
- `events.tsv`: engine frame publications, cumulative vCPU wall/CPU time and BQL waits, GL call
  count/total/max duration per publication interval, and individual GL calls/BQL waits/holds of
  at least 0.5 ms. GL timing covers the host bridge, context selection and driver call.
- `presentation.tsv`: Android post completion timestamps and separate durations for control mutex
  waits, frame copy, native-window buffer acquisition, scaling, frame pacing, posting and commit.

No guest package update is needed. Profiling is activated dynamically by the button; it does not
require `IT_VCPU_ACCT` or `IT_GLES_PROF`, and does not enable per-instruction instrumentation.
`LT_PROFILE=off` (or `0`) disables the engine recording API for comparison. The additional timers and
buffered writes run during a capture, so captures are diagnostic runs, not uninstrumented performance
baselines. Compare changes with interleaved A/B pairs using the same capture settings.

## Reading the timings

Engine publication and Android post completion are different events. A late engine publication
points toward guest/engine work or main-loop delay. A timely engine frame followed by a long Android
buffer wait points toward presentation/backpressure. Post completion is not physical display scanout.
The engine also retains the intended LCD timestamp, so publication delay can be inspected separately.

Counters overlap: GL runs inside vCPU execution, and GL wall time includes host descheduling and
synchronous driver waits. GL totals are charged when a call finishes; a call crossing a frame boundary
can exceed that frame's duration. Individual slow-call intervals allow overlap to be calculated.
The vCPU snapshots are approximate, sampled from another thread, and can lag by one execution slice.
BQL wait intervals are stronger evidence than a lock hold alone; correlate their timestamps.
Static screens and game pauses also produce long gaps; interpret them using the scene you recorded.
Outside-vCPU time includes guest idle, queued work and waits; it should not all be called scheduler
stall time. These captures do not identify every guest function or measure GPU execution time.
Use an attached simpleperf call-graph profile for a CPU-heavy scene, or a supported GPU/system trace
when the remaining question is asynchronous GPU work or precise host scheduling.

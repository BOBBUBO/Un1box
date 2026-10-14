The SDL frontend runs a fresh native worker for each session and consumes the
Portable C ABI. It does not assemble machine arguments or duplicate firmware logic.
Control/status use a private Unix socket; frames use a readonly shared memfd and PCM
uses a separate bounded socket. Worker crash and graceful shutdown are distinct.
Shared host policy is `LT_SHUTDOWN_CONFIRMATION_TIMEOUT_MS` (30 seconds) and
`LT_SHUTDOWN_GRACE_MS` (35 seconds before parent disposal), declared in the native
header and consumed by Linux. Frontends may extend observation; a shorter timeout
can interrupt a legitimate shutdown and must preserve dirty state. Startup/import
have separate budgets. Actual confirmation can complete sooner.

```sh
cmake -S Linux -B /private/build/linux \
  -DLIGHTTOUCH_QEMU_SOURCE=/path/to/portable-qemu-ios
cmake --build /private/build/linux -j2
mkdir -p /private/devices/n72
/private/build/linux/lighttouch-linux --import /private/n72ap-7E18.itbase \
  --sha256 EXPECTED_HASH --base /private/devices/n72/base
/private/build/linux/lighttouch-linux --base /private/devices/n72/base \
  --state /private/devices/n72/state --engine /private/libqemu-arm.so \
  --rom /private/bootrom_240_4 --package /private/armv6.itpack
```

Durable native library operations retain both boards and per-device state/assets:

```sh
/private/build/linux/lighttouch-linux --library /private/devices/library --import /private/n72ap-7E18.itbase \
  --sha256 EXPECTED_HASH --name "Baseline iPod" --rom /private/bootrom_240_4 --package /private/armv6.itpack
/private/build/linux/lighttouch-linux --library /private/devices/library --list
/private/build/linux/lighttouch-linux --library /private/devices/library --record RECORD_ID
/private/build/linux/lighttouch-linux --library /private/devices/library --engine /private/libqemu-arm.so
```

The selected ID persists in the shared domain; the frontend never derives paths from
a board name. A session selects the exact record in its worker, so changing selection
does not redirect a running lease. Importing another board cannot replace baseline
state. Engine facts/capabilities are queried separately per recorded board.

The n81ap kboot route omits `--rom` and uses the matching armv7 package. `--recover`
permits cold reboot of an interrupted state without deleting it. Without this flag,
dirty state produces an actionable error. Exit code zero requires confirmed guest
shutdown. Escape/window-close requests guest shutdown and waits 30 seconds in the
worker; timeout kills the worker and preserves `.dirty`. Starting again always execs
a fresh worker, never reinitializes QEMU in the SDL process.

Left/right mouse buttons own stable contacts 1/2. Native SDL touch IDs map to stable
per-contact IDs; synthesized touch mouse events are ignored. H/P/+/- control Home,
Power and volume; Space pauses and R rotates the displayed guest. S captures a live screenshot. Focus loss and
display rotation cancel all input; native mapping uses the same rotation/letterbox
transform as Android. F6 schedules a clearly
labeled two-contact pinch, not guest acceptance evidence. Arrow keys replay labeled
acceleration only. `--script FILE` runs deterministic production input commands:

```text
# seconds kind arguments
30 touch 1 0 0.3 0.5
30 touch 2 0 0.7 0.5
30.1 touch 1 1 0.2 0.5
30.1 touch 2 1 0.8 0.5
30.2 touch 1 2 0.2 0.5
30.3 touch 2 2 0.8 0.5
31 button 0 1
31.1 button 0 0
45 guest frontmost
46 guest launch com.apple.mobilenotes
# tap the actual Notes editing field through the shared input path before typing
50 guest type Hi
51 guest sync
52 guest frontmost
60 stop
```

The worker polls asynchronous touch-batch admission. An engine rejection releases
all contacts and reports its request/result through typed control messages; the
frontend clears held contacts. Queued/delivered host input is not guest-visible
acceptance evidence.
Pause clears frontend contact ownership as well as the native owned inputs.
If the bounded control socket rejects an input/release/cancel, the frontend retains
one cancellation obligation and blocks newer input until cancellation is queued.
Cancellation retries before each loop's newer events; no stale input backlog is
replayed. Shutdown delivery failure remains explicit and cannot claim a clean exit.

Touch phases: down=0, move=1, up=2, cancel=3. Motion arguments: field bits (1 accel,
2 gyro), ax/ay/az in m/s², gx/gy/gz in rad/s. Sensor replay is not physical hardware
evidence. Observe actual guest responses to qualify gestures/gyro. No guest gyro
capability is fabricated from host hardware.

`touches COUNT ID PHASE X Y ...` submits all listed contacts as one native batch
with one timestamp, preserving simultaneous down/move/lift. The parser rejects
duplicate IDs, excess contacts, truncated lists and coordinates outside 0..1.

For actual guest qualification, `scripts/make-touch-acceptance.py` creates private
offline Safari page/load and two-contact/lifecycle schedules. It only writes files;
it never starts a guest or writes device state. For example:

```sh
python3 Linux/scripts/make-touch-acceptance.py --output-dir /private/touch-test \
  --start 60 --address .5 .08 --clear .9 .08 --go .92 .95
```

The example field coordinates are placeholders. Launch Safari through the typed
`guest launch com.apple.mobilesafari` operation, inspect its screenshot/frontmost
reply, dismiss any bookmark sheet through real touches, and measure the address,
clear and keyboard Go positions. The current verified type helper calls the focused
delegate's `insertText:`; it neither selects/clears the field nor submits navigation.
The generated load script taps those measured controls and captures the typed URL
and loaded page. Confirm the colored A/B/C ruler loaded before running the gesture
script on the retained page or combining both schedules before their final Stop.

The page is a bounded `data:text/html` URL with no network or installed probe. Its
DOM counter reports actual active/max contacts and guest touch identifiers. Compare
before/during/after pinch ruler sizes, crossing identifiers, independent lift and
cancel captures with the typed Safari frontmost replies. Repeat after pause/cancel,
then perform rotation/letterbox and focus-loss checks in the live SDL window using
R and actual focus changes; a headless cancellation replay does not qualify those
window transitions. Scripts and admission logs alone do not verify guest gestures.

Status logs report native produced frames and their host-monotonic timestamp,
captured/pulled/dropped PCM frames, bounded native queued packets, socket drops,
received nonzero samples, SDL queued/discarded frames and SDL queue errors. These
separate producer/transport/sink failures; queued or nonzero samples alone do not
prove audible playback. Run without `--no-audio` for concurrent qualification and
use a supported guest audio workload; the offline zoom page itself is silent.

A stock n72 audio candidate needs no download, SDK, installed IPA or generated
audio asset: the guest screenshot shutter. The existing engine source
`hw/arm/ipod_touch_i2s.c` documents Home+Power held about350ms as producing
photoShutter.caf PCM; the same notes say keyboard clicks were silent on those
images. Bulk `guest type` calls the focused text delegate, so it cannot stand in
for a keyboard sound test. This historical source evidence proposes a target test;
it does not qualify Linux/Android output.

`scripts/make-shutter-audio-acceptance.py --output-dir /private/shutter-evidence
--start 60 --cycles 4` writes a bounded script after root has observed Home/unlock
and a real zoom page. Each cycle holds two stable contacts while pressing Home0
and Power1 together for350ms, moves both contacts, releases both buttons and
contacts, then captures the actual frame. Initial/final guest frontmost replies
check the chord did not lock or leave the app. Run with default audio enabled and
compare timestamped frame/touch activity with received PCM nonzero samples,
peak magnitude and loud-sample counts (absolute S16 magnitude>1000). The floor is
a diagnostic observation; it is not an automatic audio pass. Listen to the real
output, inspect pixels/contact identifiers and retain the exact script/log. Do not
use `--no-audio` or a dummy host audio sink for playback qualification. Each stock
shutter creates a Photos screenshot in the writable guest overlay; the generator
caps cycles at16, never modifies the immutable base and never starts a guest.

For a built-in zoom target, use Photos and a guest-created screenshot. On the
verified n72 image, wake with Home held150ms, wait2s, then drag the lock slider
from(.18,.9) to(.92,.9). Open Photos, inspect its loaded screen, open Saved Photos
and an image. Album/list loading can take several seconds; a blank list is not the
image viewer. A viewer has a `1 of N` heading and the actual full image. F6 enlarges
that image with two stable contacts101/102 over13 batches at80ms spacing
(960ms total). Keep the image below maximum zoom so a later replay can show change.
A screenshot of the stock Home screen provides readable icon sizes for comparison.

An already running private SDL guest can use the bounded controller:

```sh
DISPLAY=:99 XAUTHORITY=/private/xauthority \
python3 Linux/scripts/run-photos-concurrency.py --window 2097173 \
  --output-dir /private/photos-concurrent --execute
```

For calibrated readiness, start one bounded private worker with `--trace-io`,
`--seconds 200` and a private `--screenshot` path. At about60s, run a single wake
cycle, leaving time to inspect the actual pixels before100s:

```sh
DISPLAY=:99 XAUTHORITY=/private/xauthority \
python3 Linux/scripts/calibrate-wake-unlock.py --window 2097173 \
  --viewport 480 800 --log /private/session.log \
  --output-dir /private/wake-cycle --execute
```

The cycle balances Home150ms, observes2s, retains a native wake checkpoint,
performs the verified slider and observes2s again. The viewport must be measured
from the actual portrait SDL window; guest dimensions come from the native BMP.
It makes no readiness assertion and does not select state or launch a guest.
Inspect its fresh native PNGs and their receipts before opening Photos. Repeat a
bounded cycle only when the actual pixels require it, retaining failed attempts.
`capture-native-checkpoint.py` can retain additional native checkpoints using the
same window/log/output arguments. It copies the unchanged native BMP and its
flushed capture receipt, source-frame and worker-diagnostic CLOCK_MONOTONIC times,
then creates a viewable PNG. A stale or sleeping observation cannot admit Photos.
After inspecting a current Photos image viewer, admit the concurrency controller;
the launch owner still requires the final clean receipt.

Launch with `--trace-io` to retain worker touch admission request IDs/phases,
engine application timestamps/results, actual frame-copy sequence/source times,
and individual nonzero PCM packet receipt times/sample indices. These are host
CLOCK_MONOTONIC observations. PCM capture-relative timestamps retain their separate
epoch; receipt does not prove hardware playback. Match applied begin/move/lift
requests to the two contact IDs and require nonzero packets and actual frame
updates inside that observed interval. Queued admission alone is insufficient.

Supply the actual private display/window and a new output directory; omit
`--execute` to inspect the plan without sending input. The controller captures the
actual X11 display, balances Power1/Home0, admits F6, then starts another complete
replay at pulse+1.2s to cover the measured delayed shutter PCM burst. It records
start/end host-monotonic timestamps for every command and requests shutdown with
Escape. The launch owner must retain native status, frame sequence/timestamps,
PCM nonzero/peak/drop/error counters, source/binary/engine hashes, worker PID/start
identity and the final confirmed1/dirty0 receipt. It does not start a worker,
select state, assert success, or kill a process. Inspect Photos identity and image
scale in before/during/after pixels; rotation alone does not prove cancellation
of active contacts. No controller timing qualifies PCM/touch overlap without
reviewing target observations. Guest-scoped `IT_I2S_DUMP` can retain raw I2S data;
leave tone, gain and hardware overrides unset.

Actual private Linux GUI attempts on2026-10-14 showed a guest-created Photos image
enlarge with F6, stock shutter nonzero PCM, and clean shutdown. Run04 enlarged the
image while the screenshot buttons were held, but its sampled PCM rise straddled
the first replay's final lift. Its subsequent replay was at apparent maximum zoom;
a later held-mouse/rotation sequence returned to the grid. Those observations do
not qualify sustained simultaneous audio/touch or subsequent cancellation/zoom.
The repeatable second-replay timing above remains a candidate for target execution.
A later measured attempt did not reach a visually confirmed Photos viewer:
manual captures were black sleep checkpoints, while an independent native capture
showed the lit Lock Screen. Agent frontmost replied Home Screen, which alone did
not establish unlock. Keep each image with its capture/frame timestamps and its
own sleep/backlight diagnostics; later status rows cannot describe earlier pixels.
No concurrency controller was admitted in that attempt.
Host PulseAudio was `auto_null`, so physical audibility remains unqualified.

The native recipe supplies `-audio driver=none`, which selects the default mixer
backend while the exported capture API supplies the real PCM to SDL/AudioTrack.
The I2S source warns that a bare `-audiodev` without a default audio backend can
drop board audio; the current shared recipe does not take that route. N72's default
audio hardware includes CS42L58, LM48821, I2S/PL080 pacing and AMC. Do not disable
it with inherited `IT_AUDIO_HW=0`. If the shutter yields silence, preserve the
candidate failure and inspect guest button/frontmost response, actual mixer/capture
counts, codec/amp route and sink errors before proposing a new workload. No synthetic
host PCM qualifies the guest route.

For n81 supported-board input/GLES2 checks, Linux can use the supplied read-only
prepared folder directly with a separate private state, avoiding an18GB import:

```sh
/private/build/lighttouch-linux \
  --base /private/n81-handoff/fixture/n81ap-8C148 \
  --state /private/target-state/n81 --engine /private/libqemu-arm.so \
  --package /private/n81-handoff/release-guest-unpacked/guest-tools/armv7.itpack \
  --headless --seconds 90 --script /private/n81-input.txt \
  --screenshot /private/n81-frame.bmp
```

Source/metadata review confirms the supplied format1 lock uses kboot, nor and
NAND final `listing_sha256` (not the pre-seal `built_listing_sha256`), with nine
NAND files including geometry. Native admission hashes every NAND byte at each
open; resource allocation for that full18GB read remains necessary. It does not
copy the NAND base. Source root/files must already have no writable mode bits;
never chmod or repair the supplied original. Native open copies only the1MiB NOR
into private writable state, creates its independent NAND overlay, pins the base
lock and holds its exclusive lease. The route supplies kboot/nand-overlay/nor-rw
and the two identity die-id words; it uses no hardware ROM or n72 memory override.
The raw matching armv7 package must be passed explicitly because the shipped
lock's package path names its original Mac host. Folder import/library import would
copy the full base and should not be used merely to run this Linux reference.

The supplied base metadata/read-only modes were inspected without a full NAND
hash or guest run. This is admission planning, not integrity or target acceptance.
The current frontend worker-handshake budget is60s including base verification;
if allocated full hashing exceeds that, record the startup timeout and extend the
diagnostic startup policy instead of bypassing hashes. N81 screenshots/frontmost
and shared multi-touch/GLES2 workloads need fresh target evidence; borrowed Mac
Home/reboot results do not qualify target behavior or n81 native preparation.

`--headless --seconds N --screenshot /private/frame.bmp` captures at the duration checkpoint before requesting shutdown, then exercises the same worker,
input and frame transport without a window. Screenshots require an actual published
guest frame; there is no fallback image. Use `--no-audio` only when testing unrelated
headless behavior; default audio uses the real mixer/SDL path. Keep screenshots,
logs and firmware outside Git. The CLI implements device selection/import/errors;
SDL provides display/input/audio and state in its title, not a full desktop library UI.

Guest script commands are typed domain requests: ping/frontmost/launch/type/sync and
`gyro-probe INSTALLED_AUTHORIZED_BUNDLE_ID`. A launch/type request is queued, then
its actual guest status/output is printed when received. Requests are not retried.
Missing/stale guest agents and absent guest gyro capability are explicit errors.
Native package selection uses a checksum-declared prepared guest.itpack when present.

A requested screenshot is written at the stop checkpoint before any power-off input;
it survives shutdown timeout/worker failure. A live S capture uses the configured
path or state/screenshot-SEQ.bmp. Atomic BMP encoding is in the shared native domain,
so it needs no Photos or SDL image encoder. Capture requires an actual complete
published frame and never invents one.

Gyro/motion qualification is DEFERRED and unqualified by user scope update. The
preserved APIs/manual replay do not advertise gyro as working and do not require
SDK/probe acquisition or further gyro integration for initial completion.

Scripts can capture independent live checkpoints (`52 screenshot /private/awake.bmp`)
without stopping the guest. Status/checkpoint logs separately report backlight level,
display sleeping, agent liveness, shutdown phase and route. Black pixels are not a
shutdown confirmation. Home is button0; Power is button1. The verified original n72
wake/unlock driver taps Home150ms, waits2s, then drags (.18,.9) to (.92,.9), waits5s
and checks guest lock status. A lock screen sleeps about12s after becoming lit, so
sending a swipe to an already sleeping panel does not establish an input-map defect.

The reusable [n72 wake/unlock script](scripts/n72-wake-unlock.txt) captures the awake
lock screen and the result after the measured slider gesture. Replace its private
output paths and adjust the schedule for the observed boot time. Inspect the pixels
and guest frontmost replies before claiming an unlock. Notes text persistence needs
a separate capture after editing, confirmed shutdown, then a fresh worker reopening
the same state; an initial type reply alone does not verify persistence.

Shutdown is queued to the runtime supervisor. Control/status continue to respond
while the engine admits the agent halt or board gesture. Observer phase1 is host
queued,2 cancelling,3 engine request,4 admitted and5 actual PMU confirmation.
A halt reply or dark screen never clears dirty state without PMU confirmation and
successful worker exit.

`scripts/run-private-acceptance.py` records reproducible n72 import/write/read
commands and native state/PID observations. Its default is plan-only; `--execute`
runs the reviewed command. Output directories must be new and outside Git:

```sh
python3 Linux/scripts/run-private-acceptance.py --mode write \
  --binary /private/build/lighttouch-linux --base /private/base --state /private/state \
  --engine /private/libqemu-arm.so --rom /private/bootrom_240_4 \
  --package /private/armv6.itpack --output-dir /private/write-evidence
```

Review command.json/input.txt, then generate a new directory with `--execute` when
the state lease is free. Write mode uses the root-inspected n72 Home Notes icon and
corrected create control (.94,.09); remeasure these positions if the Home layout or
Notes screen differs. `--recover` is an explicit first-write option for a prior dirty
state. After a confirmed clean exit, run `--mode read` on the same base/state with
a different output directory, deliberately without recovery. Inspect its Notes list,
then supply `--reopen X Y` from the actual saved row pixels to capture the marker.
Link a prior driver write using `--previous-write /private/write-evidence`; this
requires its actual clean receipt, same base/state paths and marker, then compares
worker PID/start ticks to establish a distinct fresh process. Without the link,
fresh-worker lineage is recorded as unknown, never inferred from a screenshot.
The script records frontend/worker PIDs and process start ticks, exact argv, selected
source hashes, initial/final dirty and base-pin observations, log, screenshots, and
the actual confirmed-clean status receipt. A nonzero exit, missing receipt or dirty
state fails the session check. `visual_text_verified` remains false: human inspection
of write and fresh-worker reopened pixels must establish the exact persisted text.

Prepare mode uses `--import-source` and optional `--source-sha256` (mandatory for
archives) to import an already prepared base through the same native verifier.
It does not build firmware and cannot qualify firmware preparation by itself. Use
the resulting base in the same write/read sessions; native open independently checks
all base hashes and acquires the exclusive writer lease. The driver never deletes,
repairs, or substitutes state, and its supervisor only terminates its own spawned
process group on a bounded timeout.

During execution, samples.jsonl records each observed native worker's monotonic
time, CPU user/system ticks and tick frequency, RSS, readable PSS and thread count
once per second. Process start ticks protect against PID reuse. No threshold or
phone measurement is fabricated from these Linux observations. Retain actual
duration/workload and concurrent screenshots/replies with the samples.

The read-only `scripts/summarize-acceptance.py --run-dir /private/write-evidence
--evidence-kind native-guest-attempt --output /private/write-summary.json` records
artifact hashes, clean-state receipts, cumulative I/O ranges, measured producer/PCM
rates where new logs have observation timestamps, CPU and memory observations.
Use `--evidence-kind host-probe` for legal ARM guests, synthetic inputs or engine
adapter checks; they cannot qualify real iOS graphics/input/audio/persistence. Both
classes retain `initial_gate_claims: none`. Inspect actual guest pixels, contact IDs,
audible output, persisted text, target identity and lifecycle transitions before the
integrator marks a gate. No data here qualifies deferred gyro work.

Kernel handoff diagnosis can explicitly enable `LIGHTTOUCH_ENABLE_DEBUG_QMP=ON`
in a diagnostic build and pass Linux `--debug-qmp`. The default build rejects this
option as unsupported. Android debug instrumentation calls
`lt_runtime_debug_qmp_enable` between create/start and reads the derived socket
using `lt_runtime_debug_qmp_path`; production UI never enables it. The domain adds
the engine monitor endpoint internally and accepts no caller path, argv or command.
The private socket is state/work/qmp-PID.sock; the native directory FD stays open
through engine cleanup. Its short /proc/self/fd alias avoids the Unix 108-byte path
limit. A client with a different process must open the actual path's parent directory
and use its own short FD alias; Java LocalSocket on the long path can fail. Use the
backend fixed bounded read-only snapshot client (status/CPU/register reads), retain
its private JSONL, and omit the feature from release builds. Debug snapshots do not
qualify guest boot/render/input by themselves.

A calibrated private run06 on2026-10-14 retained wake LockScreen→slider Home
checkpoints, then visually confirmed Photos1of3 before controller admission.
Trace requests19–31 and32–44 each applied both stable contacts101/102 and their
balanced lifts with engine_result0/state2. First replay had nonzero guest PCM
receipt points over163.511ms within its applied interval; the second had points
over103.381ms (8656 nonzero samples in those received packets), with actual frame
sources/copies in the same observed window. Paired native sequence249 and GUI
before/during/after show further enlargement of the same Home photograph. The
worker finished confirmed1/dirty0/exit0. This is bounded Linux trace/pixel/raw-PCM
evidence awaiting independent review; sustained concurrency, physical audibility
on auto_null, Android and full G3-I remain unqualified. Earlier calibration
failures and contrary screenshots remain retained.

For crossing and interruption stages, F7 admits17 native batches: stableIDs201/202
cross in X while retaining separate Y positions, ID201 lifts alone, ID202 keeps
moving and then lifts. C releases all contacts. Generated future replay moves
are discarded on cancel/focus loss/pause/rotation/backpressure/rejection/stop;
caller script checkpoints and lifecycle commands remain scheduled. This prevents
old replay moves from reappearing after a release. The focused queue regression
checks cancellation cannot skip the next caller checkpoint; it is host evidence.

Use `scripts/run-multitouch-interruption.py` against an already inspected current
Photos viewer. Its default prints the plan. First execute `--stage prepare` with
private window/log/output arguments to perform F5 balanced pinch-in and capture
native+GUI pixels. Inspect that result below maximum zoom before admitting
`--stage cross`, `cancel`, `focus`, `pause` or `rotation` into a new output folder.
The interruption stages begin a two-contact pinch, interrupt while both are held,
then replay a fresh pinch; each has paired native receipts and actual GUI captures.
The caller supplies the measured private X display size and finally requests
Escape, retaining the confirmed-clean receipt. A script exit or applied input
alone cannot qualify any stage.

Actual private run07 retained crossing shrink/enlargement, crossed stableIDs,
independent ID201 lift and continued ID202 movement/lift. Cancel/focus/pause/
rotation each discarded9 pending generated moves and admitted new input after
interruption; pause state and visible rotation were retained. Its attempted
instant mouse double-click reset failed, so the later captures named `belowmax`
were still at apparent maximum zoom and fresh pinch scale changes were not
established. Those labels are not assertions. All failed preparation/pixels and
clean shutdown evidence remain retained. F5 pinch-in and the generic staged
controller are the corrected preparation proposal; they have not yet qualified
fresh-pinch-after-interruption on a guest.

Linux CLI `--debug-touch-trace` opts into only the fixed existing engine event
`ipod_touch_multitouch_log` before worker start. The Linux CLI switch is off by default and calls the approved typed optional
`lt_runtime_debug_touch_trace_enable` behind `LIGHTTOUCH_ENABLE_DEBUG_QMP`.
It accepts no caller event/path/argv and changes no struct/version. It adds the fixed engine
`-trace enable=ipod_touch_multitouch_log`; old frozen83 already has this event,
so diagnostic use needs no engine rebuild. Retain `[MT guest-virtual-seconds]`
FRAME_READ/release/begin records alongside host-monotonic replay/application and
capture receipts. Those epochs are distinct. APPLIED confirms the host LCD/MT
call path, not that the guest collected a release over SPI. Do not infer collection
from80ms host pacing or a subsequent successful admission.

Run08 showed a fresh visible pinch response after explicit cancel from an
inspected fit image. Its later F5 shrink attempt applied requests48–60 but had no
new guest frames (source sequence267 unchanged); focus preparation/admission was
invalid, focus recovery remained unqualified, and pause/rotation were withheld.
The200s fallback shut down cleanly. Provisional admission text was corrected in
retained evidence, and the attempted post-exit Escape failed and is disclosed.

The staged controller now offers a separate inspected reopen route. From Photos,
`--stage reopen-albums --viewport 480 800` uses bounded150ms Back taps and captures
its result. Inspect the actual Photo Albums screen before `--stage reopen-grid`,
then inspect Saved Photos before `--stage reopen-viewer`. Inspect the new viewer
and its scale below maximum before admitting one interruption stage. Each command
uses the actual private window/log, a new output directory and `--execute`; all
screens and failures remain retained. The viewport must match the measured
portrait SDL window. This route is a source-ready proposal awaiting guest use;
its navigation timing and fit state are not assumed successful.

An exact private diagnostic launch, after hashing the cached binary/frozen engine
and verifying the prior own-state clean receipt, is:

```sh
EGL_PLATFORM=surfaceless LIBGL_ALWAYS_SOFTWARE=1 SDL_RENDER_DRIVER=software \
SDL_AUDIODRIVER=pulseaudio /private/lighttouch-linux \
  --base /private/read-only-n72/base --state /private/own-clean-state \
  --engine /private/libqemu-arm-1ffcb57d.so --rom /private/bootrom_240_4 \
  --package /private/armv6.itpack --trace-io --debug-touch-trace \
  --seconds 200 --screenshot /private/run/live.bmp > /private/run/session.log 2>&1
```

Run inside the assigned private Xvfb display. First perform/inspect the calibrated
wake/slider checkpoints, then inspect each Back/grid/reopen checkpoint. Admit
`run-multitouch-interruption.py --stage cancel --window 2097173
--log /private/run/session.log --output-dir /private/run/cancel --execute` only
from the inspected current viewer below maximum zoom. Review actual guest release
and fresh begin FRAME_READ collection, paired pixels and fresh scale response;
finish with Escape and the confirmed1/dirty0 receipt. The separate Back/albums/grid navigation stages still require pixel review before
each step. They remain proposals until exercised on the current guest.

A bounded Linux run on 2026-10-14 exercised the cancel stage with the fixed
transition FIFO engine. The inspected starting photograph showed every Home
icon row and dock at fit; the interrupted photograph grew slightly, and the
fresh pinch produced progressively larger crops in both during/after captures.
The production replay discarded ten future batches on cancel, then admitted
a fresh two-contact gesture. The worker subsequently confirmed guest shutdown
with exit 0 and a clean state receipt. This is partial cancellation/reuse evidence;
focus, pause and rotation recovery were not exercised in that run.

Interpret the fixed wire trace using the exact engine source: generated frame
reports alone are insufficient. In this run, FIFO order and subsequent complete
103/75-byte command transfers imply delivery of both END reports and FULL_END
before the fresh START reports. FRAME_READ does not include a consumed frame ID
or establish that the guest accepted its checksum. Retain the transaction lines,
paired image/diagnostic receipts and the independent visible scale response;
label reconstructed delivery as an inference. Never equate admission/APPLIED
with guest acceptance.

A later bounded Linux run also exercised X focus loss and pause/resume with the
fixed transition FIFO. Each stage began from an independently inspected fit
photograph; between stages the Back→album→grid→viewer route was inspected and
restored fit. Focus loss cancelled nine future replay batches. Pause cancelled
ten, published actual state3, then resumed to state2. After each interruption,
paired native during/after captures showed progressive enlargement from a fresh
two-contact pinch. Complete transfer counts support inferred release-before-reuse
ordering under the same trace limits above. The fresh worker ended with confirmed
shutdown, exit0 and clean state. Concurrent AVD host load was retained; this is
bounded Linux behavior evidence, not sustained performance or Android acceptance.
Rotation with held contacts remains unqualified for this fixed candidate.

The staged controller also supports retaining R90 during recovery. From inspected
portrait fit, `--stage rotate-held` starts a two-contact replay and rotates after
220ms, cancelling its remaining contacts/batches. Inspect `interrupted` native
and actual GUI captures. Use separate `reopen-albums`, `reopen-grid`, and
`reopen-viewer` stages with `--rotation 90 --viewport WIDTH HEIGHT`, reviewing each
screen before continuing. Tap coordinates are transformed from portrait guest
positions into the inspected rotated letterbox; raw BMP bytes stay portrait.
Then `--stage pinch --rotation 90` retains during/after native and GUI captures,
and `--stage portrait --rotation 90` returns presentation to portrait. F6 replay
coordinates are already in guest portrait axes; the mapped mouse navigation
separately exercises the frontend transform. Keep this distinction in evidence.

An actual bounded run exercised that sequence on the fixed FIFO engine. Rotation
cancelled ten future replay batches, the mapped Back/album/thumbnail taps visibly
reopened the same photograph at fit, and a fresh replay produced progressively
larger crops in the R90 presentation. The inspected portrait return retained the
same enlarged image. The fresh worker confirmed shutdown with exit0 and clean
state. This is bounded Linux rotation/cancellation/reuse evidence, with raw and
presentation images retained separately, not physical multitouch or Android proof.

`run-crossing-continuation.py` observes the existing F7 replay without rebuilding
the frontend. First inspect fit, perform a partial F6 followed by balanced C,
and inspect that the photograph is enlarged with pan margin. The controller
retains native checkpoints near crossing, lift and remaining-contact motion;
`capture-native-checkpoint.py --defer-preview` copies BMP/receipt first and defers
PNG conversion until input finishes. This reduces work during the short solo
motion interval. Checkpoint names express intended timing, not contact phases:
compare source timestamps with actual requests and ordered wire transfers before
qualifying the image. The owner requests Escape and waits confirmed clean exit.

A bounded run retained progressive crossing/enlargement and then horizontal image
translation while the remaining contact still existed. The last two-held pose
source preceded the lift; a subsequent source followed both ID202-only moves and
preceded final END admission. The clock-face component kept the same dimensions
and moved 26 pixels horizontally, corroborating the independently inspected pan.
The intended lift checkpoint still held a pre-lift source and was disclosed;
after-release capture reused the earlier held-motion source. These timestamps
prevent later screenshots from being presented as a different contact phase.
Clean shutdown was confirmed with exit0. Raw images, exact controller snapshots,
request/source timing, wire traces and concurrent load remain private evidence;
this is bounded Linux behavior evidence, not physical input or overall gate closure.

For a lifecycle or recovery retest, include input boundaries as distinct
observations. Begin inside the displayed image, move outside its letterbox,
then return inside while the pointer remains held. If the adapter cancels the
native contacts, subsequent MOVE/UP events must be suppressed until a fresh
DOWN. A viewport resize or Surface replacement with held contacts likewise
needs a balanced cancellation and a separately inspected fresh gesture. Retain
logical contact IDs, cancellation/rejection observations and actual wire/frame
receipts; an empty adapter error or a handled event is not guest admission.

Keep raw input source time, ordered admission time, frame-source time and capture
time separate. Millisecond source ties may receive bounded nanosecond ordinals;
a genuinely backwards source time remains a rejected gesture. Never reset a
worker's ordering merely because a View was recreated or contacts were cancelled.
A fresh process starts a new ordering scope. Host ordering tests and pending
FRAME_READ counts are useful regressions, while current pixels, complete transfer
inferences and confirmed lifecycle receipts remain separate target observations.

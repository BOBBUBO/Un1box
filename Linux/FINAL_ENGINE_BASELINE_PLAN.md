# Planned final-engine n72 verification

This next plan has not launched a guest. The prospective write controller is
`builds/runtime/final-upstream-n72/run-10-stock-staged/manager.py` in the private
workspace. Its producer pins are complete; execution remains disabled until Root
reviews this exact plan and grants one GUI. Do not substitute older engine or
frontend bytes. Native sourceb690 includes the tested contact/audio lifecycle fixes plus
lazy publisher frame storage and coherent metadata. Ten ASAN/UBSAN CTests passed;
this is host regression and SDL compile evidence, not guest input, audio,
persistence, teardown or measured memory evidence.

Use the retained **generated-stock** `n72ap/7E18` userspace base directly. This
is distinct from the imported handoff base used in final-engine attempts01–06.
Historical generated-base Home/Notes/clean Stop on another producer does not
qualify the new engine. No full base copy, repeated preparation or stand-alone
NAND hash: allocated production admission validates every required byte.

| Input | Path under the port workspace | Identity |
| --- | --- | --- |
| Frontend | `builds/runtime/linux-screenshot-source-12/debug-sanitized/lighttouch-linux` | SHA256 `3a738a5f1fb4905714b1a5bc4542e9716c35eb778ea6efe662e567ca7d5a1c0e`, sourceb6905b78; mode0500 |
| Read-only generated base | `private-assets/native-prepared-n72-trial6` | n72ap/7E18/iboot; DeviceLock SHA256 `eec93b0c9c9d33fe65e760d747d5a47d9bf1e0bca659dca280ba4c8b53ffb2f2` |
| Matched ROM | `private-assets/runtime-inputs/bootrom_240_4` | SHA256 `55f4d8ea2791ba51dd89934168f38f0fb21ce8762ff614c1e742407c0d3ca054` |
| Matched package | `private-assets/native-prepared-n72-trial6/guest.itpack` | Declared raw armv6 package; SHA256 `fe25b1406b1153837075c56944fadbac7406840667215f232d16fafca079f45b` |
| New write state | `builds/runtime/final-upstream-n72/stock-state-10` | Must not exist; never reset retained prior state |
| Engine | `builds/backend/frozen/a85e640b791aa8e1260ed2c2349e7807a14cf71e3864805b5bd3fd03717c5737/libqemu-arm.so` | SHA256 `a85e640b791aa8e1260ed2c2349e7807a14cf71e3864805b5bd3fd03717c5737`, clean source259e4ad3 |
| Complete engine manifest | Same frozen directory, `portable-manifest.json` | SHA256 `5ef1ab85eddf22b43d3183ba443bea697c79ba874248a863ef105fcbfeae046e` |
| Frontend producer receipt | `builds/runtime/linux-screenshot-source-12/producer-receipt.json` | SHA256 `bdf6ac6030fc001e8b2a76e9036a063d719cdccaaa089501ccf7934e294c1c7f` |
| Runtime dependency pins | `builds/runtime/final-upstream-n72/run-10-stock-staged/dependencies-pins.json` | SHA256 `66505e1012096a6bf0c2dca2b430fbf7f2db7fbb9cd0daa7f92618018136cdf9`; 65 exact file identities |

The domain creates its own lease/base pin, sparse NAND overlay and small private
NOR. The frontend producer receipt is retained at
`builds/runtime/linux-screenshot-source-12/producer-receipt.json`; the failed prior
Homebrew SDL2 compatibility binding remains separately retained. The new native
producer's actual guest/SDL teardown is still untested. The source-plan update
read hashes only for frontend/engine/receipts/dependencies; it did not read NAND
content, copy the base or create state. Manager startup rechecks the complete
manifest, both producer hashes and every dependency hash before spawning. The
Mesa/GLVND files are prospective dependencies; record actual mappings separately
during the allocated run. Keep original warnings,
errors, captures and failed gate receipts.

Execution-ready entrypoints are retained in the staged directory. After Root
sets its explicit execution authorization and grants one GUI, launch only:

```sh
xvfb-run -a -s '-screen 0 1000x1000x24 -nolisten tcp' python3 /home/blake/Documents/LightTouch-Development/port-workspace/builds/runtime/final-upstream-n72/run-10-stock-staged/manager.py --execute
```

Keep the manager session live. In that same awaited tool execution, wait for its
`started.json`, then await `await-first-png.py --execute` and emit its exact PNG
with `view_image` immediately. The expected initial target is
`wake-auto-01/paired/capture-01/native.png`; the returned path is authoritative.
The next tool after actual Lock inspection runs `inspect-and-unlock.py --capture
<actual-capture-directory> --output unlock-fast-01 --model-inspected-lock
--execute`. This single handler records the honest after-view time, validates a
fresh capture against the inspected Lock anchors, performs any bounded rewake,
and immediately applies the mapped slider. Do not insert a status poll or wait
for Root image review between inspection and that call. A mismatch retains both
images and requires actual inspection again, within the original deadline.

Immediately inspect `unlock-fast-01/after/native.png`; if it shows Home, the next
call is repository `Linux/scripts/record-inspected-gate.py --run <write-run>
--capture <write-run>/unlock-fast-01/after --scene home --transition-record <write-run>/unlock-fast-01/content-continuity.json --output home-continuity-01
--model-inspected --execute`. The Home/editor gate still uses full-image matching,
fresh capture2s/diagnostic1s bounds, exact current worker metadata readback and original129/179s deadlines. No ROI mask is added
for these scenes. A changed image or aged capture/diagnostics can reject despite a previously viewed
scene; that is a retained failed prerequisite, never permission to type anyway.
The independent watchdog then requests ordinary Stop and Main suppresses the
later guest/touch/button/pause script dispatches.

At Noteslaunch140/frontmost145, capture `notes-scene-01`, actually inspect the
current create control, and use the private `control.py tap .93 .085 --execute`
then `control.py capture editor-scene-01 --execute` in one tool only if that
control is currently seen at that measured location. Inspect the new empty
focused editor, then immediately record the `editor` gate from that actual
capture. A Notes list, highlight or saved body cannot admit typing. Inspect the
full unique marker after type190, save via the currently visible Done/list
control before sync220, retain typed request completion and actual pixels.
Manager `result.json` retains FINAL fields independently from real process exit
and sanitizer reports; all three must satisfy the strict checks below. It never
sets visual-text verification or guest acceptance from counters.

Proposed write-session argv, with each path expanded to the absolute workspace:

```text
<frontend> --base <base> --state <new-state> --engine <frozen-final-engine>
  --rom <matched-ROM> --package <matched-armv6-package>
  --no-audio --trace-io --debug-touch-trace --seconds 360
  --script <write-run>/input.txt --screenshot <write-run>/live.bmp
```

Use one private Xvfb display, `SDL_VIDEODRIVER=x11`, software SDL presentation,
surfaceless software Mesa engine, and an owning supervisor. Before injection,
retain frontend and direct `--worker` argv, UID/startticks/pidfds, DISPLAY/window
`_NET_WM_PID`, stdout inode, clocks, RSS/load and producer identities. The parent
permits900s admission/30s checkpoint stall/5s cancelled-admission disposal;
session360s starts after HELLO. HELLO means verified admission/frame transport,
not guest readiness. Outer1340s covers those phases plus the35s Stop grace.
Admission cancellation before engine start unwinds the lease; after engine start
use ordinary Stop. Forced known-process disposal remains failure with dirty state.

The manager arms the reviewed sleep/wake witness and independent visual watchdog
**before the first HELLO**. A sleeping RUNNING receipt under the verified owner
admits one balanced Power150ms pulse; no current or historical agent precondition
is imposed before that hardware wake. After wake, the witness requires fresh
awake pixels/diagnostics and emits the actual PNG. Await and emit that image in
the same persistent tool execution; do not detach a polling process or spend the
lit interval analysing counters. Counters do not identify the scene.

Actually inspect the full native Lock image, then make the next tool call the
single `inspect-and-unlock.py` handler. It records an honest after-view timestamp,
may rewake once if currently sleeping, retains complete current/original images,
and compares only the reviewed exact native320x480 Earth/slider-knob/lock-symbol
anchors. Clock digits, caption shimmer and all other nonanchor pixels are not
compared. Shape, UID/PID/startticks, owning worker, current window/focus/viewport,
actual RUNNING/awake/agent1/backlight and source2s/diagnostic1s bounds still apply.
Original inspection reuse is at most60s; capture work at most12s; no backdating or
scene classification is permitted. Changed anchors reject and require another
actual image inspection before the original deadline. Then apply the calibrated
slider(.18,.90)→(.92,.90) immediately and capture again. Transitional/highlight
pixels are not Home. Home/editor gates retain full-image continuity; no new clock
mask is authorized for them.

Keep keys as focused real XTest events, with no `key --window` or inherited
xdotool window stack. `mousemove --window` supplies relative coordinates for an
actual mapped pointer event. Invoke repository-owned capture/gate helpers by their
committed paths; copying the capture helper changes its repository-root contract.
The private paired handler has one native screenshot writer during unlock.

For the bounded Notes portion, create the private script before launch with these
times measured from HELLO/session start. The operator must stop before a scripted
action if its prerequisite is not met; scripts are loaded once, not a live RPC
console. This avoids asserting a fixed boot duration while retaining typed calls.
The independent `watch-visual-gates.py --run <write-run> --execute` must run before
HELLO. It uses the first complete HELLO timestamp without restarting deadlines,
binds the exact frontend UID/argv/PID/startticks/pidfd, and automatically sends
ordinary INT at129s/179s unless the corresponding inspected gate exists. Operator
polling alone is insufficient. A watchdog error after ownership is established
also requests Stop; the owning supervisor remains responsible for bounded disposal.

After independently viewing actual Home or a new focused Notes editor, call the
committed `record-inspected-gate.py --run <write-run> --capture <actually-viewed>
--scene home|editor --output <unique> --model-inspected --execute` immediately.
It records actual original inspection time and a separate fresh full-byte-matching
capture/owner/readback verification time. Supply `--transition-record
<write-run>/unlock-fast-01/content-continuity.json` for Home, or
`--transition-from <write-run>/notes-scene-01` for the new editor. The original
inspected post-action image must differ from the retained pre-action image and
have a newer sequence; those facts do not classify its scene. Do not hand-write a gate, copy another worker's
gate or relabel a Notes list/saved note as a new editor. Its current image must be
awake/agent1 with capture2s/diagnostic1s freshness and exact current worker
metadata/pixel equal-even-generation copy proof (the Status sample can lag). An unchanged source sequence/publication timestamp
is allowed; this is current retained scene observation, not redraw-performance
evidence. Publication timestamps are host CLOCK_MONOTONIC. The worker sends HELLO
then starts the engine while the parent later receives HELLO, so a first engine
publication may precede the parent HELLO receipt. Require positive/nonfuture
source timestamps, captures/inspection in this HELLO epoch and current worker
identity instead of an incorrect source>=parentHELLO check. Changed clock/caret pixels can
reject full-image continuity; retain the rejection and inspect the new image only
within the original129/179 deadlines. The admission time is not a new model-view
time. A read worker needs a separately reviewed, honestly named saved-marker gate.

```text
140 guest launch com.apple.mobilenotes
145 guest frontmost
160 screenshot <write-run>/notes-open.bmp
190 guest type LightTouch generated-stock Linux persistence run10
220 guest sync
225 guest frontmost
230 screenshot <write-run>/saved-notes.bmp
260 stop
```

Require the admitted actual Home gate before129s or cancel this attempt before launch140. The bundle
is observed in retained Linux persistence-read-04, not an inferred identifier.
After inspected Notes, use current measured UI to create/focus and admit the editor before179s;
otherwise ordinary Stop before type190. Inspect full inserted text and save to
the list before sync220. Retain frontmost reply/status, source frame timestamps,
native input admission/application, wire trace and actual text/list pixels. A
highlight, APPLIED, sync acknowledgment or positive frame counter is insufficient.
No held gesture may overlap scripted guest RPCs.

Require flushed FINAL `state=0 confirmed=1 dirty=0`, workerreaped1/exit0, no
forced disposal, phase5/route/reply and actual frontend exit0 without LSan/ASAN/
UBSAN failure, retired exact frontend/worker and available exclusive lease. Do not infer success
from exit0 alone. Preserve logs, original captures, base pin, overlay/NOR and any
dirty marker on failure. Sanitizer swapcontext warnings and EGL errors remain
in evidence even if the guest confirms Stop.

Only after that actual clean write, allocate a second sequential fresh worker
with exactly the same base/state/engine/ROM/package and no `--recover`. Use the
same140 launch/145 frontmost checkpoints but omit typing/sync. Inspect the list,
open the measured marker row by210s, capture full marker230, then Stop260. Record
different worker PID/startticks and unchanged base pin; do not reset or copy state.
Full visual text, new input response and fresh frames must be reviewed separately
from clean shutdown/process disposal. No intentional interruption is included.

After successful write and independently allocated strict same-state reread,
plan a separate Linux media/input attempt on retained clean state. Do not acquire
media/SDKs or mutate the original base. Create a stock guest screenshot using a
balanced Power-then-Home chord; inspect its actual saved photo and open it in
Photos below maximum zoom. Empty Photos or a queued shutter call is insufficient.
Use production F5/F6 two-contact replay and F7 crossing/independent lift with
actual before/during/after images, then focus/resize/letterbox/pause/rotation
cancellation and a separately inspected fresh gesture after each interruption.
Do not label a cancelled replay as a successful pinch or repeat it at max zoom.

Enable real SDL audio by omitting `--no-audio`; no synthetic PCM/tone is allowed.
For a declared60–120s overlap workload, calibrate actual stock shutter PCM and
execute only balanced chords while Photos remains visibly usable. Retain source
frame/capture and touch admission/application/wire timestamps plus nonzero PCM,
actual SDL driver/spec/depth/accepted/cleared/dequeue-estimate counters. Use20–30s
window deltas and post-pause/focus/resize fresh frame/PCM observations; cumulative
old counts cannot establish new activity. A null sink/queue/dequeue estimate does
not prove physical audibility; SDL underruns are unavailable. Read physical sink
identity and measure/listen separately if authorized. Reserve35s Stop and honour
the unchanged1340s outer cap. Report actual covered duration and failed windows;
the required1200s sustained gate is Poco, not an extra Linux duration requirement.
This later media controller/scene plan still needs Root review and allocation;
no executable media/read controller is staged by this write plan.

This bounded no-audio plan cannot qualify audio, audibility, sustained workload,
Android, physical16KiB pages, n81 input/preparation, gyro or broad gate closure.
Old n72 evidence remains intact; root independently reviews the final receipts.

Allocated run01 used frozen engine SHA256
`da0292f0ac5bff6ce067f0545c3ddb0b5da6a855c90d7fbf285853820384e4ad`, source
`b59b3620b01da9285dea3a9ee6f58c1bb3cecfc2`. Its first capture was the Apple boot
logo; delayed captures were black and the later fast Home-wake capture showed the
colored Lock Screen. Home was not inspected before the timed launch. The operator
missed the planned stop gate, retained that failure, and requested Stop before
typing. The attempt ended before type190/sync220 and did not start a read worker.
Its exit1 frontend LeakSanitizer report and missing final confirmed status remain
unqualified despite absent dirty marker and available lease. Source changes to
script suppression, flushed final receipts and scoped SDL cleanup require a new
build/test allocation; they have not been exercised by that original run.

Actual generated-stock run08 on frontend ea786/cleanup259 a85 reached a visibly
colored Lock Screen and then a knob-right transitional Lock image. It did not
admit Home before the original129s watchdog, so no Notes typing or read worker
ran. The attempt's native FINAL confirmed clean Stop/dirty0/workerexit0, actual
frontendexit0/no sanitizer failure and available lease are a bounded teardown
subset only. Captures, original clocks and failed prerequisite remain retained.
The colored Lock capture preceded its honest after-view receipt by32.17s.

Run09 changes operator orchestration, not acceptance policy or native code:
await capture and emit the image in the same exec; after actual Lock inspection,
the next tool fully awaits balanced unlock and immediately emits a current
post-release capture. No Root-message/status hop belongs inside that image/action
sequence. A transitional current image gets a direct new capture/image next; it
is never relabelled Home. Exact producer pins and all deadlines remain unchanged,
with a new small state09. Execution remains disabled until the next Root GUI grant.

The prospective next plan is run10A/B. Both are source-staged with execution
disabled. A uses the new small state10; B retains that same state only after a
genuine write. Private `run-10-stock-staged/LIVE_PLAN.md` and
`run-10-stock-read-staged/LIVE_PLAN.md` contain the exact separate launch commands
and model-observation steps. No A→B automatic start exists.

After type190 completes and the operator personally reads the full unique
`LightTouch generated-stock Linux persistence run10`, the recorder uses
`--scene written-marker --observed-marker '<exact full marker>'` before Stop260,
with the actual pre-typing editor capture as `--transition-from`. Save through
the actually seen Done/list control before sync220 and preserve complete type,
sync and visible full-body evidence. This marker observation does not replace
Home129/new-editor179 or infer text from a counter.

B uses watchdog `--saved-marker`: Home129 then `saved-marker-gate.json`179,
recorded with `--scene saved-marker --observed-marker '<exact full marker>'`
from the actually reopened full saved body, with actual pre-launch Home pixels
as transition baseline. It never calls an existing body a new editor or types
again. Its script has only launch140/frontmost145, captures160/230, frontmost225
and Stop260.

B manager refuses until Root separately authorizes it and pins A result,
started/worker identities, written-marker proof, metadata-after JSON and exact
input/session-log hashes (successful type/sync operation identity and replies)
plus the unchanged state `.base-identity` hash. It verifies actual native/
frontend clean exit0/no sanitizer/forced disposal/dirty, retired A owners,
available lease, same producer/state and hashed raw full-marker proof. All
original native/session/Stop budgets remain. State resets, recovery, copies and
manual forged gates are outside this plan.

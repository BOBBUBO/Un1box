# Planned final-engine n72 verification

This next plan has not launched a guest. Root must supply the cleanup23 Linux
engine source/hash/manifest/dependency receipt and explicitly allocate the GUI.
Engine fields remain unset; do not substitute old1ff, b59 or1398 producer bytes.
The sealed frontend is source51736577 with system SDL2, bounded audio/contacts and
acknowledged pause/shutdown controls. Its eight ASAN/UBSAN CTests passed; this is
host regression evidence, not guest input, audio or persistence acceptance.

Use the retained **generated-stock** `n72ap/7E18` userspace base directly. This
is distinct from the imported handoff base used in final-engine attempts01–06.
Historical generated-base Home/Notes/clean Stop on another producer does not
qualify the new engine. No full base copy, repeated preparation or stand-alone
NAND hash: allocated production admission validates every required byte.

| Input | Path under the port workspace | Identity |
| --- | --- | --- |
| Frontend | `builds/runtime/linux-lifecycle-source-10/debug-sanitized/lighttouch-linux` | SHA256 `32c5bb84745663732663bff629e4a3b657ede212b174aa024976f869ad3efbba`, source51736577; mode0500 |
| Read-only generated base | `private-assets/native-prepared-n72-trial6` | n72ap/7E18/iboot; DeviceLock SHA256 `eec93b0c9c9d33fe65e760d747d5a47d9bf1e0bca659dca280ba4c8b53ffb2f2` |
| Matched ROM | `private-assets/runtime-inputs/bootrom_240_4` | SHA256 `55f4d8ea2791ba51dd89934168f38f0fb21ce8762ff614c1e742407c0d3ca054` |
| Matched package | `private-assets/native-prepared-n72-trial6/guest.itpack` | Declared raw armv6 package; SHA256 `fe25b1406b1153837075c56944fadbac7406840667215f232d16fafca079f45b` |
| New write state | `builds/runtime/final-upstream-n72/stock-state-08` | Must not exist; never reset retained prior state |
| Engine | Unset | Root cleanup23 frozen source/SHA256/manifest/dependency receipt required |

The domain creates its own lease/base pin, sparse NAND overlay and small private
NOR. The frontend producer receipt is retained at
`builds/runtime/linux-lifecycle-source-10/producer-receipt.json`; the failed prior
Homebrew SDL2 compatibility binding remains separately retained. The new native
producer's actual guest/SDL teardown is still untested. Keep original warnings,
errors, captures and failed gate receipts.

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
capture/owner verification time. Do not hand-write a gate, copy another worker's
gate or relabel a Notes list/saved note as a new editor. Its current image must be
awake/agent1 with source2s/diagnostic1s freshness. Changed clock/caret pixels can
reject full-image continuity; retain the rejection and inspect the new image only
within the original129/179 deadlines. The admission time is not a new model-view
time. A read worker needs a separately reviewed, honestly named saved-marker gate.

```text
140 guest launch com.apple.mobilenotes
145 guest frontmost
160 screenshot <write-run>/notes-open.bmp
190 guest type LightTouch generated-stock Linux persistence run08
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

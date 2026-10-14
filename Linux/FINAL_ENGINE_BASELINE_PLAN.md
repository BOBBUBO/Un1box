# Planned final-engine n72 verification

This source plan has not launched a guest. It needs root's final Linux engine
source/hash receipt and an explicit guest allocation. The old private1ff engine
does not qualify the incoming CP15, target-page, MMIO-limit, RCU or MT command-echo
changes. Do not substitute it if the final producer is unavailable.

Use the already qualified `n72ap/7E18` prepared stock-userspace base directly:

| Input | Path under the port workspace | Identity |
| --- | --- | --- |
| Frontend | `builds/runtime/observed-linux-admission/final-debug-sanitized-02/lighttouch-linux` | SHA256 `b814e2d33e0876ed3b47f7115942e6a26741646e7e46eb01f932f16d462646dc`, source5128ea9f/Main bafe76a6 |
| Read-only base | `builds/runtime/devices/n72/base` | DeviceLock board n72ap, build7E18, strategy iboot |
| Matched ROM | `private-assets/runtime-inputs/bootrom_240_4` | SHA256 `55f4d8ea2791ba51dd89934168f38f0fb21ce8762ff614c1e742407c0d3ca054` |
| Matched package | `private-assets/runtime-inputs/armv6.itpack` | SHA256 `fe25b1406b1153837075c56944fadbac7406840667215f232d16fafca079f45b` |
| New state | `builds/runtime/final-upstream-n72/state-01` | Must not already exist; never replace GUI state-01 or root integration state |
| Frozen engine | `builds/runtime/final-upstream-n72/libqemu-arm.so` | Pending root's source, SHA256, dependencies and architecture receipt |

The frontend's ASAN/UBSAN host CTests5/5 and actual n81 admission/clean Stop are
retained separately. They do not qualify this final engine or actual16KiB hosting.
Record the final engine and small input hashes once at staging; let production
device admission perform its required complete NAND validation. No standalone
NAND hash, full immutable-base copy, import, firmware preparation or lock rewrite.
The domain creates the lease/base pin, sparse NAND overlay and small private NOR.

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

Start `watch-ready-captures.py` before HELLO, with exact frontend PID/startticks,
window, log, a new capture directory and `--timeout 1200 --max-captures 12
--execute`. Its fresh/nonblack capture is not a Home assertion.
Invoke that helper from its committed `Linux/scripts` repository path. Its
outside-repository evidence check derives the root from its own file location;
copying it into a private run directory changes that contract and must not be
used as an executable snapshot. Retain a source/hash snapshot separately and
validate the repository helper before launch. The visual-gate watchdog has no
such repository-root derivation and can use its reviewed private snapshot.
Independently inspect copied native pixels, then split balanced Home150ms, current wake capture,
inspected Lock Screen, mapped slider(.18,.90) to(.92,.90), and inspected Home.
Use focused XTest events without `--window`/inherited xdotool window stack; retain
the current viewport letterbox and paired native/presentation receipts. Do not
reuse the combined helper's automatic wake-to-slider sequence as an inspected gate.

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

After independently inspecting the actual current pixels, write `home-gate.json`
or `editor-gate.json` with exact frontend PID, startticks, HELLO monotonic timestamp,
`model_inspected: true`, `inspected_monotonic_ns`, scene (`Home Screen` or
`Notes focused new editor`), capture-directory path, receipt-file SHA256 and native
BMP SHA256. Both source and diagnostics must be fresh and the capture awake/agent1;
late inspection, another worker, stale capture, wrong scene or cumulative counters
cannot qualify a gate. The script never generates a scene assertion. A read attempt
needs its own read-specific gate policy; do not apply the new-editor typing gate
or silently reuse the previous worker's Home receipt.

```text
140 guest launch com.apple.mobilenotes
145 guest frontmost
160 screenshot <write-run>/notes-open.bmp
190 guest type LightTouch final-engine n72 persistence run01
220 guest sync
225 guest frontmost
230 screenshot <write-run>/saved-notes.bmp
260 stop
```

Require inspected Home by130s or cancel this attempt before launch140. The bundle
is observed in retained Linux persistence-read-04, not an inferred identifier.
After inspected Notes, use current measured UI to create/focus the editor by180s;
otherwise ordinary Stop before type190. Inspect full inserted text and save to
the list before sync220. Retain frontmost reply/status, source frame timestamps,
native input admission/application, wire trace and actual text/list pixels. A
highlight, APPLIED, sync acknowledgment or positive frame counter is insufficient.
No held gesture may overlap scripted guest RPCs.

Require terminal `state=0 confirmed=1 dirty=0`, phase5, route/reply, exit0,
retired exact frontend/worker and available exclusive lease. Do not infer success
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

# Next Linux Settings profile: two existing 40-second recordings

This is the finite source-only handoff after research04 was stopped. Root
approved preparation of **two original 40-second recordings**, each with the
existing collector's 48-second outer bound. No runtime, guest Start, GUI input,
perf attachment, ADB operation or build is authorized by this document.
Media05 stopped its worker; its old PID/window cannot be reused as a live target.

## Bind one separately owned guest

The next Runtime/Root operator must prepare a new finite guest allocation using
the existing isolated `stock-state-10` **only after** its clean/free-lease check.
Keep its read-only generated base and existing writable state; this profiling
lane creates no state, copy, lease or guest process. A missing/retired worker,
DIRTY state or absent GUI allocation stops readiness. No implicit recovery,
Start, clock reset or new full-base copy follows. Runtime retains all existing
boot, Home, automatic Stop, shutdown and resource watchdogs.

Retain this exact production pair, unless Root reviews a new packet:

| Input | Source revision | Frozen SHA256 |
| --- | --- | --- |
| Optimized Linux frontend, debug symbols/frame pointers | App `6b07201cd722602136d2207dc496faab3526d5f3` | `3b3da08d04e7b2510db4744237d855f1cfc7e6c28a0de16c0b3948fc3d27161d` |
| Production TCG engine | QEMU `e7a56d80b1ca59d1517c06fdddfb06cc89b377dd` | `7b6744eb0fb7838c78339ed4f18f45782d28099b1f8738a0f765cd62616bfde9` |

Source/binary-only checks found `.symtab` and `.debug_info` in both binaries;
the engine contains `tb_gen_code`, `tcg_gen_code`, `tcg_optimize` and `cpu_exec`
symbol names. This does not verify a live mapping. Preserve the operator's
actual frontend flags, including any existing I/O/touch tracing; do not add
per-TB logging, research switches, accounting or another engine for this run.
The two actions are first observed/repeated **within that worker**, not a
controlled guest/host cache-cold experiment.

## Exact later identity and access preflight

Only after the new guest operator gives exclusive GUI ownership and existing
worker access, record this binding in the private packet:

- Run directory, host boot ID, ordinary UID; frontend/worker PID and start ticks;
  exact full argv and parent relationship, from the new launch receipts and
  matching `/proc` evidence. Never infer current identity from media05 files.
- Actual executable-mapped engine path and SHA256; actual frontend path/hash;
  guest base/package/config identities and writable-state lease owner.
- Stable TIDs, start ticks and verified roles: vCPU, QEMU main, renderer/audio
  where present. Use the owner-verified list, not only thread-name guesses.
  Retain unknown/new/unselected thread coverage gaps.
- DISPLAY/XAUTHORITY, focused X11 window ID, `_NET_WM_PID`, portrait aspect-fit
  geometry, and fresh personally inspected awake Home capture with its native
  receipt/hash. The GUI operator alone accesses this display and writes captures.

Current host prerequisite reads found `perf` available, `perf_event_paranoid=4`,
`kptr_restrict=1` and `sudo -n true` successful. They did **not** open perf events
or inspect the live target. Use the existing collector's sudo path; actual perf
permission is established only by the separately granted recording. Do not run
an extra diagnostic attachment or change kernel settings. `ffmpeg` resolves to
`/home/linuxbrew/.linuxbrew/bin/ffmpeg`; do not assume `/usr/bin/ffmpeg` exists.

Use canonical QEMU `contrib/portable/linux-cpu-profile.py`, source SHA256
`418e0772a88d3cae9250db34f7e7a8f7c8c4d6c53d64ecdaed0c2c3ed6064873`.
Root must review a changed collector before proceeding. Set the following
variables from the actual new operator handoff; placeholders are deliberately
not invented current PIDs:

```sh
TCG_COLLECTOR=/home/blake/git-projects/qemu-ios-android/contrib/portable/linux-cpu-profile.py
PROFILE_PACKET=/private/fresh-settings-profile
PROFILE_WORKER_PID=ACTUAL_PID
PROFILE_TCG_TID=ACTUAL_TID
PROFILE_MAIN_TID=ACTUAL_TID
PROFILE_ENGINE=/exact/actually-mapped/frozen/libqemu-arm.so
mkdir "$PROFILE_PACKET/first" "$PROFILE_PACKET/repeat"
python3 "$TCG_COLLECTOR" prepare --pid "$PROFILE_WORKER_PID" \
  --thread "$PROFILE_TCG_TID:verified-vcpu" \
  --thread "$PROFILE_MAIN_TID:verified-qemu-main" \
  --engine "$PROFILE_ENGINE" --plan "$PROFILE_PACKET/first/plan.json" \
  --output "$PROFILE_PACKET/first/recording"
python3 "$TCG_COLLECTOR" collect --plan "$PROFILE_PACKET/first/plan.json"
```

Add only verified stable additional `--thread TID:role` entries before sealing
the plan; do not duplicate the main/vCPU TID. The last command preflights only.
Root reviews its exact plan/tool hashes and creates that plan directory's
`ROOT-GRANT.json`. Prepare the repeat plan afresh against the same surviving
worker immediately before the second recording, using `repeat/` paths.
An identity, mapping, producer or bounds mismatch stops the packet.

## Operator dispatch and fixed endpoints

No new controller is needed. The private readiness packet retains a byte-exact
copy of the previously reviewed `menu-burst.py`, SHA256
`b65da95dbcc62ee775938625b6a3ef40d984faf5d9f7c87c3e83e2556e03bf47`.
It uses the existing run's `control.py` (SHA256
`fc62e9993d766acbcd4eb8c6d6425eb455c6703c5930ae53be9c170c415d98ae`)
and pinned `watch-ready-captures.py` (SHA256
`3497952b88b0e15ec914490aa4cfc6042a340659fb73e2a3b3f73be4b5a53e2b`).
The new run must explicitly bind these sources and its existing operator grant.
Do not stage files into or operate an old/live run without its owner.

For **each** recording, the GUI operator first captures and actually inspects
Home and the Settings icon. Coordinates `.14,.68` are the historical n72 layout,
not authority to tap a changed scene; use only if this fresh image confirms
that icon there. The predefined target is **Settings root**: the complete
Settings title/navigation bar and root preference rows, including Wi-Fi and
General, fully painted and readable, with no launch blank, transition or modal.
A highlighted icon, title alone, touch/agent acknowledgement or frame count
cannot qualify. General submenu is explicitly outside these two recordings.

Root starts the sealed recording, not the guest:

```sh
python3 "$TCG_COLLECTOR" collect --plan "$PROFILE_PACKET/first/plan.json" \
  --execute-approved-sha256 ACTUAL_FIRST_PLAN_SHA256
```

Keep that collector alive in its own awaited execution while the **single** GUI
operator dispatches one burst after its launch receipt plus at least three
seconds. This delay is preparation, not proof that perf events are enabled.
`started.json` means the collector process was launched. Confirm recording
coverage later using raw sample/event timestamps before input; absent evidence
leaves phase coverage unqualified. Do not perturb the guest to force samples.

```sh
python3 /private/readiness-packet/menu-burst.py --run /actual/new/owned/run \
  --output profile-first-settings --x .14 --y .68 \
  --source-capture /actual/new/owned/run/actually-inspected-home \
  --model-inspected-source --execute
```

The helper arms one native capture writer before the balanced tap, captures at
most 20 native frames/2 Hz within its original ten-second operation deadline,
and retains source inspection and actual `controls.jsonl` input edges. Wait for
terminal success before any dependent capture/input. Preserve failure and stop
this comparison on any failed prerequisite; there is no automatic retry.
The operator personally inspects the retained frame sequence. Neither this
helper nor the profiler classifies scenes or proves a Settings endpoint.

Let the first 40-second recording finish; require its successful terminal
receipt and profiler retirement. Outside the recordings, the GUI operator uses
one balanced Home operation, captures and actually inspects fresh Home. Then
prepare/seal/start the second original 40-second recording and execute the
identical burst as `profile-repeat-settings` from that inspected Home. No extra
menu, Power/wake, typing, pause, recovery or guest restart is included. If the
guest sleeps or cannot return to Home, preserve the incomplete comparison.

## Admission clocks and evidence handoff

Before first recording, require at least **160 seconds** until the run's
original automatic-Stop dispatch. Budget two 48-second collector outer bounds,
up to 25 seconds for the between-profile Home handoff, and 35 seconds for the
operator's ordinary Stop margin. A run with Stop at HELLO+320 therefore admits
first profile no later than HELLO+160. If this does not fit, do not start a
profile, relax a deadline or create a replacement guest under this packet.
Profiler cleanup never signals the worker; Runtime owns ordinary guest Stop.

Retain both plan/grant hashes, collector start/terminal files, raw perf and
lost-event records, stable identities/maps, operator controls, immutable native
BMPs/receipts and honest inspection times. Index each action by actual
`mousedown`/`mouseup` command brackets, not wrapper launch. For each target,
record the last inspected incomplete and first inspected complete frame and
source-frame times; label a missing bracket honestly. A later capture may show
a static earlier source frame, so capture completion time is not repaint time.
Native content timing is not X11 presentation latency. Independently inspect
an actual SDL surface capture if claiming displayed content, and retain its
separate timestamp/overhead; do not create a surface-latency number from a late
screenshot.

After both recordings and ordinary guest retirement, use the existing offline
report command for each recording:

```sh
python3 "$TCG_COLLECTOR" report --recording "$PROFILE_PACKET/first/recording" \
  --output "$PROFILE_PACKET/first/report"
python3 "$TCG_COLLECTOR" report --recording "$PROFILE_PACKET/repeat/recording" \
  --output "$PROFILE_PACKET/repeat/report"
```

Bin raw monotonic samples into the recorded action/frame intervals. Retain
translation/optimization/emission, MMU/helper/device/graphics, other native,
unresolved and lost evidence separately. Inclusive stacks overlap self counts.
If sample counts or phase coverage are insufficient, report an inconclusive
profile. One first/repeat pair supplies a bottleneck hypothesis; it establishes
no speedup, no causal translation diagnosis and no Android/backend acceptance.

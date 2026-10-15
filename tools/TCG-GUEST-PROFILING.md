# Representative TCG guest profiling proposal

Status: source-only plan after research04's `STOP_CURRENT_CANDIDATE` result.
No guest run, new backend, CPU speedup, device qualification or profiler
allocation is claimed here. Production iOS continues to use QEMU TCG.

The useful question is why the **first observed app/menu action** is slower
than repetition on the same guest. New TCG translation is one hypothesis.
Guest executable paging, dyld work, filesystem caches, application
initialization, GPU shader work, MMIO/helper dispatch, scheduling and thermal
limits can produce the same symptom. Faster repeated actions alone do not
identify a translation bottleneck.

## Reuse the existing collectors

Use the canonical QEMU `contrib/portable/linux-cpu-profile.py` and its
`LINUX-CPU-PROFILE.md`, rather than starting another generic profiler. The
source already prepares an exact existing-worker/thread identity plan and
collects one finite 40-second native `perf` recording under an external Root
allocation. It retains raw native self/inclusive reports, timestamped samples,
unknown addresses, lost records and worker/thread CPU/scheduling observations.
No guest controls, engine flags, kernel-setting changes or guest launches are
part of that collector. Pin the canonical collector revision and bytes when
preparing the actual plan; this research branch may predate later tool fixes.

For Android, reuse canonical App `Android/scripts/profile-existing-guest.py`
with its pinned NDK28.2 inputs and documented existing-worker ownership.
Its existing recording is ten seconds, 99 Hz, explicit process/thread scope,
32-MiB output limit and bounded buffers. Do not silently enlarge these limits
or install a research04 engine to make a profile. Android `--app` can delegate
to a platform recorder; retain actual recorder/version/log evidence. A denied
recording is unavailable evidence, never a reason to change device security.

The existing `IT_VCPU_ACCT=5` fixed diagnostic can add run/halt/io/BQL and CPU
context when available in the **actual producer**. It must be configured in a
fresh worker before Start through the existing reviewed path. It cannot arm
an already consumed worker. Accounting's run wall time is not translation CPU
time and is not genuine iOS scheduler CPU usage. Do not rebuild or restart a
user's session solely on this proposal.

## First bounded actual-guest experiment

1. Freeze the current production App/engine revisions, native-library SHA256,
   build type and actual flags, guest board/iOS/base/package hashes and graphics
   provider. Leave research04 switches absent. Use the verified prepared `kboot`
   fixture and an isolated writable state. Keep the base read-only; do not copy
   its full extracted NAND unnecessarily. Preserve the user's live state/APK.
2. The exclusive guest operator boots to a visually confirmed settled Home
   screen, identifies the actual vCPU/main/render/audio threads and obtains a
   fresh existing-worker collector plan. Retain PID/TID/start identity, host
   boot ID and mapped library evidence. Collector ownership and GUI/input
   ownership must agree; starting a profiler does not grant guest control.
3. Predeclare a small navigation sequence: Home to Settings, a specific menu,
   Home, then exactly the same sequence twice. Retain input dispatch times and
   native frame plus displayed-surface evidence. Declare complete-content
   endpoints before reviewing results. No coordinate-only inferred pass,
   queued input into a sleeping surface or frame-counter-only completion.
4. Allocate one original 40-second Linux recording spanning the first sequence
   and repeats, with host-monotonic phase boundaries. Only use this plan if the
   existing guest/operator can complete those phases inside the fixed window;
   otherwise mark incomplete and propose a separately reviewed bounded design.
   Do not extend the recording or silently reclassify unfinished phases.
5. Stop the profiler normally and preserve its terminal receipt, all raw data,
   lost/unknown sample counts and exact input/frame references. The operator
   retains responsibility for ordinary guest shutdown and same-state safety.
   A profiler failure and a guest shutdown failure remain distinct failures.
6. Repeat a matching unprofiled sequence to estimate profiler disturbance only
   under a separate allocation. Keep the performance panel and screen capture
   cadence fixed; capture/monitor overhead is a measured variable. One trace
   supplies a hypothesis, not a speedup claim. Material effects need a finite
   replicated matched A/B, predefined endpoints and thermal/resource controls.

Call this **first observed versus repeated within one worker**. A fresh worker
has an empty in-memory TCG code cache but can inherit warm host file caches and
changed guest disk state. A reopened app need not be a fresh guest process.
Record both facts; do not label either condition a universal cold start.
Separate fresh-worker samples require isolated identical writable baselines and
ordinary shutdown, not unrelated cache drops, host swap changes or state wipes.

Linux/x86 profiles can reveal host-native code-generation/helper costs. They
cannot establish Android/AArch64 ratios, JIT permissions, 16-KiB compatibility,
physical GPU behavior or Tensor/Qualcomm performance. After an actionable Linux
finding, confirm it on an ARM64 Linux host and one supported physical Android
phone with the same guest action before proposing a broader rollout. Existing
virtual Pixels extend compatibility coverage; they cannot stand in for the
physical GPU or prove a new backend is fast on a phone.

## Measurements and decision rules

Report visible endpoint latency as an interval between the last incomplete
and first complete inspected frames, with input-dispatch time and capture
cadence. Retain the actual image references. Report CPU seconds independently
for the whole worker and vCPU thread, with raw units/resolution. Whole-worker
CPU includes rendering/audio/support work. Scheduling wait, run/halt/io wall
time and sampled user CPU are distinct quantities.

Native sampled **self** categories should separate:

- TCG translation driver/ARM decoder, IR optimization and host code emission.
- Already generated guest execution, only when recording-time mapping evidence
  actually resolves those addresses; otherwise keep them unresolved.
- Execution/MMU helpers, device/MMIO, graphics, and storage/I/O wrappers.
- Other resolved native work, unresolved addresses and lost samples.

Inclusive stacks help identify callers; their overlapping percentages cannot
be added to self totals. The existing collector samples user CPU, so it cannot
fully attribute kernel I/O or off-CPU waits. Read phase-binned sample timestamps;
a whole-recording average can hide a short first-action translation burst.
If a phase has too few samples or too much unresolved/lost data, report the
attribution as inconclusive. Do not convert a handful of samples to a precise
translation-time claim.

A first-action increase in resolved translation self CPU supports a translation
hypothesis. It does not prove that all visible delay is translation. Warm
native-helper/device or graphics CPU points to different optimizations.
High scheduling delay, low worker CPU or guest WFI needs wait/wakeup analysis.
High generated-code CPU with low translation points toward code quality or
helper/callback overhead, rather than cache capacity. Memory allocation/cache
size alone cannot distinguish these cases.

If native evidence remains unresolved, propose one minimal default-off,
thread-local aggregate diagnostic at a concrete QEMU boundary: translation
count and CPU duration, TB flush count/reason, or mode/ARM-versus-Thumb execution
share. Validate its actual producer and measurement overhead before collecting
results. Per-TB printing, debugger single-step and `-d exec` are inappropriate
for the primary latency comparison. `-perfmap`/`-jitdump` are available in the
inspected Linux QEMU source but are not enabled by the current collector;
they require a separately reviewed recording-time mapping/overhead plan.
Never classify anonymous samples as guest execution merely from a name.

## Execution paths worth keeping on the decision table

| Path | What makes it useful to investigate | Evidence required before implementation or promotion |
| --- | --- | --- |
| Current native TCG plus targeted fixes | Retains full-system/MMU/device behavior; known I/O replay/CP15 fixes have already improved mechanisms. Profile decoder/emitter, helper/MMIO, TB churn and dispatch separately. | A measured actual-guest cost and a concrete source change; preserve precise exceptions/timing and compare the same state/actions on Linux and Android. Previously losing TLS/lookup-cache trials are not fresh wins. |
| TCG build optimization, LTO or profile-guided optimization | Could reduce native translator/helper overhead without a second architectural engine. It does not automatically optimize the guest's generated code. | Inspect actual release flags first; a deterministic training workload, clean producer manifests and matched target A/B. No unmeasured `-O3`, device-only flags or unsafe math defaults. |
| Cache/translation lifetime improvements | Plausible when measured retranslation or flush churn dominates repeated actions. Cache size helps only demonstrated capacity pressure. | Actual miss/flush/retranslation evidence and memory bounds. Preserve code permissions, SMC, address-space identity and invalidation. Raw generated-code persistence across workers is not an existing supported feature. |
| Deeper alternate optimizing DBT | Potentially improves hot guest code if it can retain execution for meaningful spans while preserving full-system state. | Representative privileged/EL0 and ARM/Thumb hot-code share, data-MMU/fault/IRQ/CP15/device contracts and total boundary cost. Research04's losing short-block capsule is stopped; do not grow its whitelist to postpone that decision. |
| KVM/HVF/Android AVF | Useful for an outer Android VM when host/guest hardware and permissions fit. | Inner ARM1176/A32 privileged guest capability, compatible board/system semantics and access from the intended app. An x86 KVM Android VM still translates inner ARM iOS; an ARM64 outer Pixel on M4 does not establish A32 EL1 or Android app-UID acceleration. These are not selectable faster iOS backends today. |

Modern full-system LLVM/DBT designs can inform a future architecture. The
inspected HQEMU source targets obsolete QEMU/LLVM and lacks a demonstrated
Android/AArch64 integration for this guest. User-mode libraries likewise do
not supply the ARM1176 privileged machine or board bridge. Unicorn/TCI are not
established performance upgrades here. No concrete alternate full-system CPU
backend has been qualified, and no hardware virtualization switch is missing
from the current product UI.

Cached upstream references already show QEMU commits `2e1e7e611e` (I/O limit
correctness), `97860b36b0` (frame publication at painted vsync), and `cb3595e8ad`
(configurable panel/touch rates). These are useful source-review leads, not new
CPU engines or a fresh remote-head audit. The portable fork already includes
related I/O-limit correctness work; compare exact hunks before duplication.
Frame publication may reduce display latency independently of CPU translation;
raising refresh rate can increase work. Root's upstream/integration owner must
review and qualify any chosen import. Mac-only performance evidence never
closes Android/Linux guest acceptance.

# CPU execution alternatives

Research checkpoint: 2026-10-15. No alternative CPU engine is qualified for an
iOS guest. The production engine remains QEMU native TCG. This document is an
engineering experiment plan, not a supported-backend list.

## Retained research source

The optional probe and harness history is preserved under the explicit
`archive/cpu-hybrid-research04-20261015` tag in both forks. App tag source is
`7286bbdc49475e7572cc610dd1b92f45b20c1b6e`; QEMU tag source is
`979fb0edaed94c5efb756df550d3ca128728d3ec`. Use those tagged checkouts to reproduce
the dated synthetic experiments below. The current main branch retains this
outcome and profiling plan without adding the rejected optional engine to
production sources. Production build pins and APK contents do not change.

## Current decision: stop research04 hybrid expansion

The default-off Dynarmic throughput candidate **lost its actual finite CPU
comparison**. Decision: `STOP_CURRENT_CANDIDATE`. Preserve its source, tests,
immutable binaries and failed evidence; do not expand its instruction coverage,
port this candidate to Android, or expose a product CPU option on this result.
Real iOS sessions continue to use TCG. This rejects the tested snapshot/handoff
integration, not every possible use of Dynarmic or optimizing translation.

Tested QEMU source: `979fb0edaed94c5efb756df550d3ca128728d3ec`.
Corrected harness/measurement source: App
`0b751e692df984e8c44c1d016839db10f508847d`.
Immutable Linux/x86-64 research binary SHA256:
`014be9bc297f65b60f9cecfae6d2cd0f431587c13a509c4cffda78ee92a39aaf`.
Library: the previously pinned Ubuntu Dynarmic 6.5 package. No ARM64, Android,
real-board or iOS speed qualification follows.

Three fresh processes per route ran the 65,536-iteration four-instruction
ADD/SUB/CMP/BNE loop: 262,144 user instructions, followed by SVC and privileged
WFI. OFF and ON used the **same binary**. Six additional fresh one-iteration
samples observed setup; orders alternated across three replicates. Every sample
passed its full sixteen-register/CPSR terminal oracle and ordinary native exit.
All owned processes retired. ON proved one cold block and 65,535 warm blocks
in each long sample; OFF had no alternate execution witnesses.

| Complete workload CPU delta, three long samples | Same-built TCG OFF | Candidate ON |
| --- | --- | --- |
| Whole process, 100 ticks/second | 0, 0, 0 ticks | 10, 10, 10 ticks |
| vCPU thread, 100 ticks/second | 0, 0, 0 ticks | 10, 10, 10 ticks |
| Conservative two-tick decision interval | 0–20 ms | 80–120 ms |

Zero reported TCG ticks means the measurement did not resolve its cost; it does
**not** mean TCG took zero CPU time. Do not divide by zero or publish a slowdown
ratio. The intervals are the predeclared quantization allowance, not statistical
confidence bounds or an allowance for every source of host noise. Nevertheless,
all three alternate measurements clearly lose under that rule for both scopes.
Cold one-iteration measurements never enter the speed decision.

The long samples' first preparation/allocation/translation/Run interval was
18.771–22.740 ms. Their following aggregate span was 80.127–85.083 ms. These
are monotonic host intervals including adapter/loop work, not pure JIT CPU
measurements. They show that removing the first cold interval does not remove
the remaining cost. The experiment does not separately attribute the loss to
MMU checks, state copies, dispatch or generated instructions; do not infer a
specific winning replacement from these totals. The 1 ms completion polling
and minimum wait also prevent using outer wall time as precise guest latency.

Correctness evidence spans two attempts. Original research04 attempt01 remains
**FAILED**: its first fifteen stages passed, then async live polling repeatedly
parsed an earlier log prefix until the unchanged 16,384-block cap was reached.
The engine exited normally and all owned processes retired; no performance
sample was accepted from that failure. App `0b751e69` bounds live reads to the
current phase while retaining whole stopped-log and full-state validation.
A separate ten-process attempt02 passed blocks OFF/ON/stock, corrected async,
and throughput one/65,536 OFF/ON/stock. Together, the preserved first attempt's
passed stages and corrected attempt establish 44 complete historical case
states equal across OFF/ON/stock, 3,592 corrected async witnesses, exact
throughput coverage and stock negatives failing solely missing witnesses.
Do not describe this as one successful 22-process attempt or erase attempt01.

Each runtime stage retained its 60-second/256-MiB owned-RSS/8-GiB host-available
bounds, one serial engine and 600-second outer limit. The twelve-sample
measurement actually finished in 2.341 seconds, peaked at 83,607,552 owned RSS
bytes and retained at least 17,663,590,400 available host bytes. These are the
recorded guardian observations, not an exhaustive peak-memory proof.

Private receipts remain outside Git beneath the workspace's
`builds/cpu_alternatives/research-04/`:

- `contracts-actual-01/RESULT.json`, SHA256
  `a29bb9c26f460c43676c52ab192234b61ae1d327696dbe13ba3763c4507c2f5c`.
- `contracts-actual-02/RESULT.json`, SHA256
  `7ee2b0f367c88747b870fc791eb4d3a99943ce291978643fec3149974fd22a48`.
- `measurements-actual-01/RESULT.json`, SHA256
  `cee97c738a53768ea791e19699fafd58d4d0771cab299a59feedacfaffa95c11`.

Root reviews and a separate worker offline receipt/log revalidation accompany
these results. No new engine run is implied by offline review. The next useful
work is [representative actual-guest TCG profiling](TCG-GUEST-PROFILING.md),
using existing bounded collectors before another backend integration.

The dated sections below preserve the experiment's progression. Statements
marked source-only or unrun describe their historical checkpoint; the current
result and stop decision above govern research04.

## First concrete experiment: Dynarmic contracts

Dynarmic supports ARMv6K guests, AArch64 and x86-64 hosts, and Android. Its
architecture availability therefore fits the n72 target. It is a CPU library
with user-mode semantics, not an ARM1176 full-system replacement.

Current source inspection used the maintained
[Azahar fork at a466015](https://github.com/azahar-emu/dynarmic/tree/a46601580d5512d324104f985b5f0209dc980ddc).
Important actual boundaries are:

- CPS, RFE and SRS request an external interpreter. The library supplies none.
- MSR control-field updates do not implement privileged mode transitions.
- Internal A32 register state has no ARM banked registers or SPSRs. Its block
  identity does not include CPU privilege mode or a guest address-space ID.
- Coprocessor callbacks can implement CP15 operations, and memory callbacks can
  delegate memory access. These are extension points, not an existing MMU.
- `check_halt_on_memory_access` and execute-denial callbacks provide useful
  hooks; precise architectural abort state must be demonstrated separately.

`dynarmic-contract-probe.cpp` prepares eleven actual JIT cases at ARMv6K: CPS/RFE/SRS
fallback, attempted SVC-to-IRQ mode change, CP15 read/write callbacks, SVC,
MMIO, generic-halt negative control, explicit MemoryAbort and execute denial. Expected unsupported behavior is
deliberately reported rather than emulated by a fake interpreter. A successful
probe means the documented boundaries were observed, not that privileged
instructions or an iOS guest work.

The first actual Linux/x86-64 run compiled successfully and observed the first
eight boundaries. It then failed its data-fault assumption: generic
`HaltExecution()` sets `UserDefined1` and lets the block finish. Source inspection
identified the specific `HaltReason::MemoryAbort` check. The original failure is
retained; it is a probe API mistake, not a demonstrated library defect. A second
finite probe is prepared with the original generic halt as a negative control
and the specific memory-abort path checked for preserved destination register,
fault PC and suppressed following instruction. The second actual Linux run
passed all eleven cases: the generic-halt negative executed the next instruction,
whereas explicit MemoryAbort preserved r0, retained PC=0 and suppressed the next
instruction. Execute denial reached the callback but returned JIT PC=4; a real
adapter must retain fault-event context and implement architectural exception
entry. This result qualifies these small library contracts only.

Example build against an externally supplied Dynarmic installation:

```sh
clang++ -std=c++20 -O1 -Wall -Wextra -Werror \
  tools/dynarmic-contract-probe.cpp -I"$DYNARMIC_PREFIX/include" \
  -L"$DYNARMIC_PREFIX/lib" -Wl,-rpath,"$DYNARMIC_PREFIX/lib" \
  -ldynarmic -o /tmp/dynarmic-contract-probe
timeout 10s /tmp/dynarmic-contract-probe
```

The prepared Linux experiment uses Ubuntu's SHA-verified Dynarmic 6.5 package,
privately extracted with its runtime dependencies. This differs from the
maintained source inspected above and does not establish Android compatibility.
Retain exact package, source, compiler and result hashes with each run.

The most plausible first hybrid would leave kernel/exception/device execution
in QEMU and attempt eligible user blocks in Dynarmic. Before a real guest, it
must prove:

1. Bidirectional integer/VFP/CPSR state synchronization and bounded interrupt,
   pause and shutdown exits.
2. Faulting-instruction PC and register precision; memory permission checks and
   abort entry without unsafe unwinding through C++ JIT frames.
3. Address-space and privilege-separated code identity, self-modifying code,
   CP15/TLB changes, executable remaps and all existing invalidation routes.
4. Existing n72 **PL0 CP15 QEMU_CALL** and host-clock callbacks. Guest graphics
   uses this bridge; handing only SVC back to QEMU is insufficient.
5. Actual identical-state guest workloads with memory callbacks enabled. A
   register-only microbenchmark cannot predict a full-system speedup.

This is a substantial accelerator integration, not a selectable build flag.
No speedup magnitude or engineering completion date is established.

## Concrete QEMU boundary and asset-free reference

The production shared library exports `qemu_ios_*` lifecycle/command APIs;
it does not export `CPUARMState`, the ARM MMU walk or CP15 implementation.
An external caller cannot use those APIs to implement a genuine full-system
Dynarmic memory adapter. A successful mock callback would not resolve this.

The first real in-engine experiment should be disabled by default and attempt
one whitelisted EL0 instruction before normal TCG lookup, at
`accel/tcg/cpu-exec.c:cpu_exec_loop`, after interrupt and breakpoint checks.
It must clear `last_tb` when switching execution engines and retain existing
virtual-clock, exit, single-step and instruction-budget behavior. Unsupported
instructions, all privileged execution and the board CP15 bridge initially
remain on ordinary TCG. A separate default-off QEMU prototype implements this
strict synthetic instruction whitelist; production builds do not enable it.

Relevant current ARM interfaces are concrete:

- `cpsr_read` / `cpsr_write` reconstruct cached condition flags, change modes
  and bank registers. Copying only `uncached_cpsr` is incorrect. VFP registers
  use `aa32_vfp_dreg`; FPSCR requires its accessor. An initial integer-only
  instruction whitelist can defer VFP execution, not silently discard state.
- `arm_cpu_tlb_fill_align(..., probe=true)` checks the actual MMU without
  delivering an exception. Permissions, access width/alignment, MMU index and
  physical address-space attributes must all be preserved. Debug memory APIs
  used by GDB are unsuitable for runtime guest permission enforcement.
- The same helper's fault path calls `cpu_restore_state` and the static
  `arm_deliver_fault`, which exits QEMU execution nonlocally. A Dynarmic memory
  callback must first retain exact fault context and return through its JIT;
  architectural fault delivery must occur back in QEMU. Calling this path
  directly across active C++ frames would be unsafe.
- Every relevant QEMU TLB/code invalidation path must invalidate the alternate
  cache or advance its context identity. Initially flushing on every handoff
  is conservative for a correctness proof, with no performance expectation.
- `hw/arm/ipod_touch_2g.c` registers PL0 CP15 QEMU_CALL, and
  `hw/arm/guest-services.c:qemu_call` reaches the real GLES service. A generic
  machine-none test cannot prove this board registration or GLES dispatch.

`arm1176-mmu-reference.py` uses an already built engine, `-M none`, ARM1176,
16 MiB legal synthetic RAM and an 8 MiB TCG cache. GDB loads real short
descriptor page tables and CP15 bootstrap instructions, then observes actual
EL0 register/mode/fault results. It requires an external owned-process
resource/deadline guard; there is no engine build, iOS fixture, display or
Android device involved.

The first actual run passed MMU bootstrap, EL0 readable load, permission abort
and translation abort. It failed an execute-never assumption: the current
QEMU `get_S1prot` explicitly clears XN before ARMv7, so ARM1176 executed the
load and encountered its unmapped data address. This is retained as a failed
test assumption and an existing implementation boundary, not advertised as
architecturally correct execute-never behavior. The second actual run at
`70a4bbe5` passed six narrow cases: it isolated that behavior with a readable
data address, checked data-abort LR values and observed genuine unmapped-fetch
prefetch abort at PC=0x0c, LR=0x401204, mode=ABT. Both harness and QEMU exited
normally, with no owned process left. This does not validate XN protection.

The later harness adds MOV/ADD cases and an optional requirement for exact
in-engine Dynarmic execution witnesses. A separate actual Linux/x86-64 build
at QEMU `653c948ab2` passed the eight-case same-binary OFF/ON differential:
complete sixteen-register/CPSR observations matched, OFF emitted no bridge
witnesses, and ON emitted both actual Dynarmic execution witnesses. A stock
engine passed the state assertions but correctly failed the witness-required
negative. The real library was Ubuntu Dynarmic 6.5. The two eligible integer
instructions executed in Dynarmic; all data loads, CP15 bootstrap and faults
remained on real TCG. This is a default-off synthetic integration proof, not
a useful workload benchmark or alternate iOS backend qualification.

The six-case reference establishes expected QEMU state; the separate
two-instruction extension proves actual synthetic in-engine Dynarmic execution.
Neither proves the board CP15/GLES bridge, qualifies an iOS guest or establishes
a speedup. There is no measured EL0 CPU-time share on which to base a hybrid
performance prediction.

## LLVM and other paths

| Candidate | Source-backed assessment | Next decision |
| --- | --- | --- |
| HQEMU TCG + LLVM hot traces | Genuine full-system optimizing DBT design. The inspected public [2.5.1 source](https://github.com/sailfish009/hqemu/tree/b48995c43003abf39bb4d423e93b4e3adc293a79) is based on QEMU 2.5.1, has LLVM 3.5/3.8/3.9 patches, x86-specific register/patchpoint support, and trace-prolog handling in its x86 TCG backend absent from its AArch64 backend. | Preserve as an architectural reference. Rebase to current QEMU plus an Android AArch64 optimizing backend is a major project; do not start an obsolete full build as if it tested our guest. |
| Learned direct translation | The [2024 system-level paper](https://arxiv.org/html/2402.09688v1) implements ARMv7-to-Intel-x86 on QEMU 6.1 and shows why MMU/helper/state coordination can erase a code-generation win. | Useful design evidence, not an ARMv6-to-AArch64 implementation. Do not copy published speed ratios into LightTouch claims. |
| Direct translation headline results | The [2025 proposal](https://arxiv.org/html/2501.03427v1) evaluates a restricted RISC-V interpreter with a simpler memory interface; its proof is not a full-system ARM implementation. | No testable drop-in backend for this guest. |
| KVM / Android AVF | Hardware execution must support the guest ISA and privileged state, in addition to granting access. The [KVM API](https://docs.kernel.org/virt/kvm/api.html) makes 32-bit EL1 a host capability. [Android AVF](https://source.android.com/docs/core/virtualization/architecture) is not a cross-ISA translator. | Existing app-UID KVM access observations remain separate. An x86 outer Pixel accelerated by KVM still translates inner ARM iOS. No privileged-device changes are proposed. |
| Unicorn / TCI | Unicorn inherits QEMU translation; TCI interprets TCG operations. Neither represents an established faster CPU path for this workload. | Keep as possible diagnostics, not a performance substitution. |

Dynarmic is 0BSD; its optional dependencies must be reviewed for the selected
build. HQEMU inherits QEMU GPL terms and separately patched LLVM licensing.
No package or distribution clearance follows from a research-only probe.

## Prepared follow-up: checked RAM, flags and branches (not run)

QEMU source now prepares actual data-MMU-checked aligned positive-offset LDR
word snapshots, broader r0-r12 immediate ALU operations, CMP/NZCV, and
B/BEQ/BNE. The C++ bridge receives only a value capsule and verifies the
actual JIT result against complete per-instruction integer/CPSR expectations.
Rejected access/width/opcode/region or any state mismatch preserves original
QEMU state for ordinary TCG. No live QEMU helper enters a C++ callback.
Breakpoints/watchpoints reject the alternate path conservatively.

`arm1176-dynarmic-contracts.py` is a new **UNRUN** follow-up input. Its three
fresh-process suites use no firmware: eight ordinary integer cases, eight
flag/branch cases, and eleven memory/fallback cases (only three eligible loads).
Memory negatives include actual MMU permission/translation/alignment faults,
non-whitelisted byte/writeback/SP forms and Thumb fallback. Alignment tests
prospectively set SCTLR.A in the real CP15 bootstrap; no observed outcome is
claimed. Same-binary OFF/ON runs must compare every sixteen-register/CPSR
observation and exact execution witnesses, then a stock witness-required run
must fail even if its TCG state observations pass. Preserve any failed input
assumption separately, as with the historical pre-v7 XN correction.

A bounded eight-step snapshot planner/capsule is source preparation only.
**The live engine hook still passes budget=1 and requires GDB single-step.**
No free-running/block caller is enabled; bypassing interrupt/debug/timer exits
would be incorrect. No alternate iOS, board CP15/GLES, Android, AArch64,
interrupt/lifecycle, cache/invalidation or speed qualification follows.
The fresh 8 MiB JIT per invocation is still a correctness scaffold.

The follow-up actually executed at QEMU `c6a943c63a` with this harness source
`4632ba2c`: all35 complete integer/CPSR states matched OFF/ON/stock across four
fresh-process suites. Exactly23 actual Dynarmic execution witnesses occurred
only on ON (4historical,8integer,8flags/branches,3loads). All four stock
witness-required negatives passed their state assertions and correctly failed
only missing alternate execution; all12 QEMU sessions exited0/retired. Real
TCG permission/translation/alignment/prefetch faults and byte/writeback/SP/
Thumb fallbacks remained equal. The larger capsule has no enabled block caller;
no iOS/Android/AArch64/interrupt/lifecycle/cache or speed result is established.
Exact source/binary/library hashes and raw private evidence are retained in the
integration receipt; these real synthetic observations do not advertise a
production backend improvement.


## Research03 prepared useful blocks (SOURCE ONLY / NOT COMPILED OR RUN)

`arm1176-dynarmic-blocks.py` proposes a concrete next correctness milestone
for the default-off QEMU `LT_DYNARMIC_BLOCK_PROBE=1` caller. It is independent
of the historical single-step switch. One genuine `Jit::Run` executes a
branch-terminated two-through-eight-instruction plan, using a retained private
eight-MiB JIT and one cached signature. Code/MMU/RAM identity is checked each
entry, dynamic data snapshots are refreshed, and TCG handoffs invalidate code.
Only MachineNone/ARM1176/EL0/little-endian/single-vCPU is eligible. Full final
integer/CPSR state, exact ticks and ordered read counts must agree before commit.
The exact engine contract and unqualified boundaries are described in QEMU
`docs/devel/lt-dynarmic-probe.md`.

The new harness is **UNRUN**. `--suite contracts` has nine observations:

- Dynamic-load three-iteration loop, then changed-code loop at the same PC.
  Each must really execute three five-instruction Dynarmic blocks with fresh
  read results42/99/123, one JIT allocation and two cache reuses.
- Three-instruction block followed by real data permission abort, and separately
  unmapped instruction-fetch abort; normal TCG delivers the true fault state.
- Unsupported SVC tail and eight instructions with no terminal branch must
  reject the entire alternate block and execute TCG.
- Single-step, actual GDB breakpoint and actual read watchpoint remain TCG.

The contracts matrix must compare complete16-register/CPSR states from the
same-built OFF/ON and stock witness-required negative. ON must emit exactly
its expected genuine block witnesses; OFF/stock must emit none. Stock must
pass every state assertion then fail solely the missing-witness requirement.
`--suite async --require-block` is a distinct ON-only proposed test: stop a
running two-instruction loop, match committed PC/r0 with the last witness,
resume and require a warm hit, then change code while stopped and require
fresh compilation. It fails if the16384-block diagnostic cap is reached.
This tests QMP pause/resume only, not hardware IRQ/timer injection or iOS
lifecycle. Each running phase keeps its original5s deadline; total harness
observation limit25s, ordinary QMP quit, external process-tree guard required.

The current source has not been compiled or target-executed. LLVM instruction
disassembly, Python AST parsing and source diff checks are the only proposed
pre-build validation. No phone, AVD, fixture, historical SDK, product default
or canonical branch is changed. Historical tested research02 binaries and
raw receipts remain preserved; its correctness result does not qualify this
new caller. A separate reviewed finite build and runtime allocation is needed.

The snapshot planner evaluates every expected instruction in C and emits
per-block diagnostic output. This is a correctness scaffold, not a measured
performance candidate. A later go/no-go decision must measure representative
EL0 coverage and separately quantify translation, warm compiled execution,
MMU preparation, callbacks and cross-engine handoff. ARM64 Android/iOS,
Thumb/stores/VFP/CP15/device services and asynchronous device timing are still
unqualified. All real boards retain TCG.


## Actual research03 contracts and prospective async correction

QEMU `12c2c7bc6e` built and actually ran against App harness `be4477b2`.
Independent review compared all44 complete16-register/CPSR observations across
OFF/ON/stock: historical35 plus nine useful-block contracts matched exactly.
ON produced23 historical Step witnesses and eight genuine Run witnesses;
OFF/stock produced none. Five stock negatives passed every state assertion
then failed solely missing alternate witnesses. Each native engine exited0.
Actual five-instruction loops read42/99/123 and reused compiled code; true
faults, unsupported tails, branch-budget exhaustion and debugger fallback
remained correct in this synthetic scope.

The aggregate attempt remains **FAILED**. The async test incorrectly required
its very first resumed block to reuse compiled code. QMP Stop can leave an exit
request that conservatively takes the TCG handoff and invalidates the cached
block. Recorded execution correctly continued r0 from200 to3318, compiled a
fresh generation2, then produced3117 genuine warm hits. This is useful scoped
evidence, not a passing async suite; its code-modification phase never ran.
All original logs and failed receipts remain unchanged.

The prospective harness correction changes acceptance only. It preserves engine
exit/invalidation behavior, requires exact per-block r0/run continuity and full
stopped register/CPSR equality, and requires two consecutive genuine warm hits
within the first eight resumed blocks. A resumed first block may either reuse
its previous generation or compile a newer generation with code reads. Changed
code must always cause a fresh generation and compilation; stale reuse fails.
The original25s total and5s phase deadlines,16384 cap and normal QMP exit remain.
The corrected live test is UNRUN. Offline checker review of the two recorded
phases and mutated-evidence negatives does not backfill the failed attempt.

After corrected async execution, the next useful CPU milestone is a separately
reviewed synthetic throughput candidate: separate cold translation and warm
execution costs, eliminate diagnostic per-block printing and duplicate C
instruction-result interpretation, retain actual MMU/fallback/exit safeguards,
and compare finite identical workloads against TCG with external final-state
oracles. Count total process CPU time including preparation and engine handoff;
a raw JIT-only win is insufficient. The current tiny-block snapshot interpreter
is a correctness scaffold and has no measured speed benefit. Before applying a
hybrid engine to iOS, measure representative actual EL0 hot-code coverage and
qualify Thumb/stores/CP15/device/interrupt boundaries and ARM64 deployment.
All real iOS boards and production Android builds still use TCG.


## Research03 corrected runtime and research04 throughput preparation

Root executed the corrected four-process plan once against the unchanged
QEMU12c2c7bc6e binary. Its scoped result passed: nine complete OFF/ON/stock
block states matched; corrected async pause/resume/code-change contracts
validated14083 real Run witnesses with exact stopped state. Every owned
process retired normally. Original failed attempt01 remains FAILED. This is
synthetic x86/Linux correctness, not an iOS/ARM64 or speed result.

Research04 is SOURCE ONLY. The independent `LT_DYNARMIC_THROUGHPUT=1` switch
requires the existing explicit block switch and MachineNone eligibility. It
retains the original two-through-eight instruction/MMU/context/debug/exit
boundaries, but checks instruction structure rather than interpreting ALU
results in C. Only existing r0-r12 immediate ALU/CMP/terminal branches are
admitted; loads stay on the prior correctness path. Unwritten registers,
protected CPSR bits, terminal PC possibilities, exact tick budget and absence
of data callbacks are checked before commit. Correctness mode still checks
full expected state. The external oracle must check exact changed values.
Per-block logging is replaced with one segment aggregate containing actual
committed instruction/block counts, cold/warm totals and boundary timestamps.
The throughput diagnostic cap is1048576 blocks, then TCG. No default changes.

New UNRUN `arm1176-dynarmic-throughput.py` proposes a finite four-instruction
countdown loop with1 or65536 iterations, SVC and privileged ARMv6 CP15 WFI.
It checks all16registers/fullCPSR against a closed-form terminal oracle. The
ON aggregates must prove exactly4*N alternate instructions across all segments,
one cold block and segment-blocks-minus-one warm hits per segment, with exact
cumulative PC/flags/countdown values. Ordinary async exits may split a workload;
total native CPU time includes that cost and cold/warm counts remain measured. Same-binary OFF and a
stock witness-negative must produce identical full terminal state; missing
execution evidence cannot masquerade as a qualified alternate path.

No observer pause/input occurs while the finite workload runs. The observer
records complete process and vCPU-thread CPU ticks/identity and uses a sleeping
vCPU only as a completion hint, then verifies actual terminal state. A stall
fails the exact state gate. Wall observation has1ms polling/at least5ms wait;
it is not precise guest completion latency. Linux CPU ticks may be too coarse
for a short cold run; report raw ticks and resolution, never divide zeros into
a speed ratio. Aggregate first/cold-end/last clocks bracket actual preparation,
allocation/translation and host-loop work, not pure JIT costs. Cold1 and warm65536
fresh-process samples distinguish setup from repeated complete execution; even
a favorable isolated x86 result would not predict iOS performance.

Go/no-go path: measure complete candidate CPU cost against same-built TCG;
attribute any loss to MMU/snapshot/dispatcher boundaries before extending scope.
If safe boundary work dominates, stop this hybrid direction. If a material
benefit is plausible, measure unchanged real-iOS privileged/EL0 and ARM/Thumb
hot-code share before spending on missing instructions. Then qualify live data
MMU, faults, SMC/context invalidation, actual device IRQ/timer/debug and board
PL0 CP15/GLES handoffs. Run equivalent differential contracts on ARM64 Linux,
then Android app-UID JIT/cache/lifecycle and4/16KiB hosts, before an isolated
real-guest A/B. No useful coverage or end-to-end gain means no production option.
This source does not add a modern phone CPU engine or claim a speedup.

The finite CPU decision rule uses three fresh65536-iteration samples per route.
CPU tick deltas receive a conservative two-tick uncertainty interval for
separately quantized user/system accounting. Only a robust20% reduction in both
whole-process and vCPU cost advances this direction to actual iOS coverage
research. A clear CPU loss stops this candidate; overlapping/zero-resolution
measurements establish no benefit and do not justify instruction expansion.
Cold1 samples never enter a speed ratio. Host evidence tests reject missing/
duplicated instruction segments, corrupt terminal state/timing, malformed
witnesses, incorrect process identity parsing and fabricated zero-tick gains.
These host tests qualify receipt validation only, never an engine or guest.

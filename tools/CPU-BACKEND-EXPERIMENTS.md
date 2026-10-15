# CPU execution alternatives

Research checkpoint: 2026-10-15. No alternative CPU engine is qualified for an
iOS guest. The production engine remains QEMU native TCG. This document is an
engineering experiment plan, not a supported-backend list.

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

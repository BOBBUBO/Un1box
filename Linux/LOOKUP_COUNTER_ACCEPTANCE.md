# Lookup-reason diagnostic acceptance

Use the experimental counter engine only in an explicitly allocated diagnostic
run. It adds hot-path work and is unsuitable for ranking action latency against
an uninstrumented production build. Retain the actual engine/frontend hashes,
producer revisions and cache geometry. An older diagnostic engine's lack of
newer shutdown tracing must remain visible in its results.

The finite first/repeated Settings workload uses the same confirmed-clean small
writable seed, current optimized frontend and inspected source/target scenes.
Preserve normal device admission, exclusive lease, Home deadline, Stop deadline,
shutdown grace, immutable-base checks and whole-task retirement. Do not reset or
recover a seed to force a successful comparison. Do not mix this diagnostic with
perf recording, per-TB traces, audio, periodic guest RPCs or unreviewed VM flags.

Set `IT_VCPU_ACCT=1` before loading/starting the engine. Its cumulative
`[tb-cache]` records report `fast`, `empty`, `pc`, `state`, `slow-hit` and
`slow-absent`. The elapsed accounting time printed in those records has its own
origin; it is not host monotonic time, worker start time or HELLO time. The vCPU
outer loop emits records at most once per accounting interval. Execution and
stdio delays mean there is no strict one-second publication guarantee.

For each personally inspected Settings action:

1. Before input, retain the latest complete counter record, its raw byte line
   offset and hash, and the host-monotonic file-read bracket.
2. Retain actual mouse-down/up brackets and every original native capture. The
   operator identifies the last transition sample and first complete Settings
   frame from those images; changed pixels or counters do not classify a scene.
3. After the complete target, record the log EOF and read bracket. Wait a bounded
   five seconds for a new complete record whose line *starts* at or after that
   EOF. Completing an older partial line is insufficient. Preserve failures.
4. After strict Stop and whole-task retirement, hash the final log. Use the
   reviewed offline `contrib/portable/tb-cache-delta.py` parser to validate and
   subtract the selected cumulative records. Keep original witnesses unchanged.

A newly appended record proves publication after the witnessed EOF, **not** that
counter values were evaluated after the target frame: `fprintf` can wait for a
stdio lock after evaluating arguments. Describe these as covering observation
windows with outer-loop, publication, operator and idle uncertainty. Do not
claim exact action-only counts or derive speedups from them.

Interpretation stays narrow:

- `empty` means a null selected cache slot, including cold or invalidated slots;
  it does not independently identify code generation.
- `pc` means a conflicting virtual PC in that slot.
- `state` means the same PC with a context/flags mismatch; it does not prove an
  ASID problem or justify dropping context comparisons.
- `slow-hit` reuses an existing physical-QHT TB; `slow-absent` finds none.
  Translation faults/longjmp can leave a miss without a returned slow result.
- Direct TB chaining bypasses this lookup. The counters are not a denominator
  for all guest blocks, instructions or MIPS. Inclusive lookup profiles may
  already contain MMU/TLB work; do not sum overlapping percentages.

Malformed, partial, mixed-owner, reset, changed-geometry or misordered evidence
must fail validation. An incomplete counter window remains incomplete even if
native shutdown later succeeds. Native clean Stop, visual scene observations,
count distributions and measured latency are separate results.

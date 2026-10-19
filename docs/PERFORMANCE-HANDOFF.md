# Performance handoff — 2026-10-19

## Resume state

App and engine work belongs on `dev/perf-codex`; do not merge into `dev/perf`.
Read `/home/blake/goal.md`, [PERFORMANCE.md](PERFORMANCE.md), [TODO.md](TODO.md)
and [PROFILING.md](PROFILING.md) before resuming. The previous performance goal
was replaced by the user's request to wrap up, commit and push. Stable 60 fps
with clean 1% lows on the Snapdragon 865-class Poco F3 has **not** been proved.

All measurements are stopped. The final runner completed two focused pairs
and one ordinary title pair, then was intentionally interrupted before the
next launch. Exit 130 is cancellation; its finally cleanup completed, no qemu
processes remain and the Poco's exact original debug-env was independently
verified. No device configuration should be inferred from an old active-arm
file. Original bytes: `build/perf-codex/tally-batch-original-debug-env`.

Latest engine: `20857608bb`. Latest APK uses app source `3ab7fb07`; subsequent
app commits only update documentation. `build/android/LightTouch-debug.apk`
matches frozen `build/perf-codex/tally-batch.apk`, SHA256
`60fe67e03c51e403477f0e8b567abbea86e453c4d72783c9f6cb0b10e03ac0c9`.
It was installed on the Pixel at the user's request; installed bytes match,
app data/debug settings preserved and MainActivity opened. Pixel BQL switches
remain default off. Poco has private guest tools serial 29/version 1.1.27 with good 29
verdict; do not assume Pixel was updated to serial 29. Do not publish guest packages.

## Established gains and limits

- BQL 50 µs spin: ordinary Poco Jetpack title 53.422 → 57.472 fps (+7.58%);
  hitches 30 → 8 per 180 s/mode. Extending 50 → 150 µs in a separate comparison:
  57.156 → 58.361 fps (+2.11%), hitches 16 → 11. Both experimental/default off;
  Pixel/other games and longer gameplay are unmeasured. Do not combine these
  separate sessions into a cumulative percentage.
- Client-array caching defaults on. Pixel setup/cleanup 0.619 → 0.228 µs/draw;
  about 10% better best gl-draw times. Consistent game-level gain unproved.
- Latest logging batching: focused reversal does not repeat the first local
  benefit. One ordinary pair 59.033 → 59.333 fps, hitches 1 → 0, insufficient for
  repeatability. Keep default off. Full pacing tables are in PERFORMANCE.md.
- Recent title windows near 59 fps with median/ceiling 60 agree with the user's
  observation of a smooth title screen. Roughly 30 fps 1% lows and occasional
  long gaps remain; sustained gameplay needs separate evidence.

## Best remaining leads

1. **Recurring refresh misses and the work preceding them.** Four fresh sparse
   captures have 50/50/44/45 gaps >30 ms; many adjacent held ticks are 88–92 ticks
   apart (about 1.5 s). This is a new cadence observation, not proof of a clock
   defect. Start offline with `wrapup-frame-leads.py/json` under
   `build/perf-codex/`, and `poco-tally-batch-focus-2-off/late30.json`. Follow
   exact previous game-present and queue-insertion boundaries to the next
   selection/base write. Retain preceding history; counting only work inside
   a visible gap misses its start. Compare ordinary intervals too.

2. **Audio bursts before shorter gaps.** In existing `poco-audio-queue-1`,
   looking back one frontend interval finds AAC running for 48/87 gaps >30 ms;
   42 have >5 ms, 28 have >10 ms. These overlapping, variable-length windows are
   clues, not independent samples or causal producer boundaries. The same
   capture has 1881 converter calls: output aligned to 4 bytes median 24.635 µs,
   output 2 mod 4 median 420.235 µs, with occasional multi-ms batches. Preserve
   ARM1176 alignment faults and iOS handler behavior. Next join exact batches
   to the recurring misses and identify selected-thread/kernel work before
   choosing an optimization. AAC includes conversion; never add their totals.

3. **Logging/service outliers.** The same capture's two >50 ms gaps have
   preceding producer intervals 47.029/47.764 ms with 25.681/25.717 ms
   launchd+syslogd running and exact tally markers before the visible gaps.
   Neither wider producer interval contains AAC. Both suppression and new
   batching lack an established repeatable pacing win. Next attribute the
   service execution to actual stderr/IPC functions or another request before
   spending more ordinary pairs on logging. Preserve logs/counters/failures.

4. **Bluetooth startup/retry work in the largest outliers.** Earlier
   `poco-ca-path-1` contains a 166.135 ms gap with 83.682 ms Bluetooth-owned running.
   `poco-queue-path-1` records a 10 s BTServer timeout/relaunch despite every
   observed HCI reply being injected. Healthy follow-ups do not reproduce it.
   Next capture UART FIFO drain/timeout/IRQ and userspace receipt around a
   reproduced failure, with fresh same-worker logs/owners. Injection is not
   receipt; preserve protocol/service behavior and avoid firmware payload logs.

5. **Pixel gameplay pauses need Pixel evidence.** Its earlier 214.5 ms gameplay
   gap had about 205.9 ms execution wall but 83.2 ms vCPU CPU and 3.2 ms GL. Existing
   user recordings lack the host scheduler trace needed to classify the rest.
   Capture a synchronized scheduler/native profile with pre-hitch history
   during the actual scene. Do not transfer Poco conclusions to Pixel.
   Gravity Guy's early long pauses were user-confirmed loading/menu transitions.

6. **Residual host waits and BQL holders.** 150 µs spin reduces short blocking
   fallbacks, but longer waits/guest idle wake delays remain. Identify actual
   lock owners and main-loop work before extending budgets again. Prior idle
   spin and priority experiments lack a repeatable gain; keep them off. PL080
   audio DMA per-element dispatch and remaining LCD/BQL work remain original
   structural leads, not newly measured improvements.

Other original leads remain in TODO: dynamic TLS access cost, TB/softmmu paths,
Pixel texture upload, upstream-sync phone validation and desktop frame wakeups.
Read rejected experiments before selecting one; avoid unchanged retries.

## Measurement discipline and local artifacts

Ordinary A/B uses the same frozen APK/package, interleaved order, all diagnostics,
full accounting and user-facing profiling off. Sparse diagnostic FPS is not an
ordinary gain comparison. Keep every new engine experiment behind a switch;
time small paths directly and preserve ceiling/median/low/1% low/hitches.

`build/perf-codex/tally-batch-results.json` contains six completed arms with
exact windows, pacing, identity, scheduling and BQL outcomes. Its analyzer only
includes arms with finished markers; remaining planned arms are not results.
All twelve completed-arm before/end screenshots were visually reviewed as
Jetpack title with Start profile off. Raw data and private packages are local
and ignored by git. `wrapup-frame-leads.py/json` retains new offline counts and
input hashes; `continuation-status.md` records final local state.

Phones: Poco `192.168.1.55:42041`; Pixel
`adb-62250DLCR00320-FmrX3q._adb-tls-connect._tcp` (last address
`192.168.1.94:40995`). Recheck connectivity and availability. Always set an
explicit serial, check stray qemu processes, and restore exact settings after
measurement. Do not create three Pixel state copies. About 45 GB desktop space
was free at wrap-up.

# Planned Linux interrupted-session recovery check

This is an execution plan, not retained guest acceptance. It requires a new
allocation for two sequential bounded guests and the intentional termination
below. Do not run it while another lane owns Linux guest resources. No engine
build, phone, AVD, firmware preparation, or original-fixture mutation is needed.

The existing cached SDL frontend is SHA256
`fddeed4c169f383b7d3cf76989d3f22776314275f4aa3aed2b3f2116d555d46f`.
Use the privately frozen engine SHA256
`1ffcb57dab386ad2e144337e7670ea4b058ad18608500b6c7d72db2624ba03ff`,
source `b88aaabee7b57427bbb68f345eae3878716266db`. These bytes predate the RCU
ownership correction; this plan cannot qualify that correction. Any new engine
requires its own frozen source/artifact receipt and explicit allocation.

Set up a new private evidence directory and a separate empty writable state,
for example `builds/runtime/gui-acceptance/recovery-state-01`. Use the existing
read-only `builds/runtime/devices/n72/base`, matched ROM and guest package from
`private-assets/runtime-inputs`. Do not clone a running state or overwrite the
retained clean `state-01` used by runs09–13. Domain admission creates the lease,
identity pin, NAND overlay directory and small writable NOR; do not copy the
immutable NAND. Record hashes of the executable, engine, ROM, package and exact
script snapshots. Record `.dirty` existence, identity pin and lease inode before
and after each phase. Never infer lease release from a missing GUI window.

Each graphical session uses one assigned Xvfb display, one native worker,
software rendering, `--trace-io --debug-touch-trace --seconds 360`, and a private
screenshot path. Retain its exact argv, frontend/worker PID, `/proc` start ticks,
X display/window, monotonic launch time, source logs and concurrent host load.
Use a supervisor fallback after the frontend's shutdown grace, not an earlier
kill deadline. The plan is not a sustained-performance claim.

1. Start without `--recover`. Independently inspect current native wake/slider
   images using `calibrate-wake-unlock.py`; wait for an actual visible Home,
   rather than proceeding on a highlighted icon or transitional lock frame.
   Open Notes using measured current pixels, create a unique bounded marker,
   save to the list, and request the typed `guest sync` operation. Retain the
   returned status, list/reopened-marker pixels and source/diagnostic timestamps.
   A sync acknowledgment alone does not prove later persistence.
2. After the saved marker is visibly inspected and the current guest PID/start
   ticks are verified, intentionally terminate **only that owned worker** with
   SIGKILL. This destructive step requires the allocation to explicitly include
   interrupted-session recovery. Let the frontend supervisor observe failure;
   retain its terminal receipt/log instead of substituting a clean exit. Verify
   the worker and frontend have exited and the writer lease is available. Keep
   the entire failed state, `.dirty`, base pin, overlay, NOR and logs intact.
3. Attempt normal admission of the same state without `--recover`. Record the
   native worker refusal text and output logs: `interrupted session: reopen with
   explicit cold recovery; data retained`. Core returns `LT_DIRTY`; the cached
   CLI prints the message rather than the numeric code. When the rejected worker
   exits, its parent reports `worker control channel closed`; that generic
   channel error alone is not the dirty-policy result. Admission that remains
   busy for 60 seconds instead produces `worker startup timeout`, also not a
   dirty-policy result. The expected refusal starts no engine and does not clear
   the marker or modify user data.
4. Start the same state in a fresh worker with explicit `--recover`; record the
   different PID/start ticks and unchanged identity pin. Inspect actual Home,
   Notes list and reopened full marker. A title alone is insufficient for the
   full marker check. Retain typed frontmost replies and paired current pixels.
   The existing overlay is deliberately reused; this is a cold recovery, not a
   fresh prepared-base boot or snapshot restoration.
5. Balance contacts/buttons, request ordinary stop, and permit the shared
   30-second confirmation/35-second disposal budget. Require the actual guest
   confirmation, terminal exit0, dirty0 and released worker/lease. If shutdown
   fails, preserve the failed receipt/state and leave the result unqualified.

Inspect intermediate pixels locally and continue when valid; root reviews the
retained evidence independently and owns final gate closure. The plan's result
must distinguish visible marker persistence, dirty admission policy, explicit
recovery, guest shutdown confirmation and host process disposal. Do not turn
synthetic contract tests, a typed request admission, exit0 alone, stale images,
or host-created screenshot files into a guest persistence assertion.


Cached CLI ownership and timeout details
---------------------------------------

The fdde frontend matches `Linux/Main.cpp` at3db9bc70; its source is unchanged
through integrated app53ea5309. The frontend forks and execs its own executable,
appending `--worker CONTROL_FD PCM_FD`. Identify the guest by that exact argv
flag, its matching base/state/engine paths, direct parent relationship and
`/proc/PID/stat` start ticks. Other descendants, including a desktop audio daemon,
are not the guest. Verify identity immediately before any allocated signal;
prefer a retained Linux pidfd to prevent PID reuse. Never signal a cached PID,
`pgrep` name match, shared audio process, or another owner's process group.

`--seconds 360` begins after admission/HELLO and SDL initialization, not at process
launch. The separate pre-HELLO deadline is60seconds. Stop is asynchronous; the
worker permits30seconds from native Stop admission and the parent35seconds from
its submitted Stop. Both elapsed checks use a fresh monotonic timestamp and
reject negative elapsed intervals. An outer launch supervisor must permit these
phases (for example480seconds total), then retain an interrupted result if it
must dispose the exclusively owned group. Its timeout is not clean shutdown.

For refusal use the same `--base`, `--state`, `--engine`, `--rom`, `--package`
arguments, omit `--recover`, and optionally add `--headless --seconds 1`; no
window is needed to observe admission. For the recovery session add `--recover`
only, keeping the identity paths unchanged. `--debug-touch-trace` requires the
existing debug-enabled frontend build; `--trace-io` prints host-monotonic
observations. A GUI stop is Escape; script `stop` also requests ordinary shutdown.
The historical `run-private-acceptance.py` write/read scripts contain fixed
45-second navigation and are not a substitute for current inspected readiness.
No command or intentional interruption in this plan has been executed.

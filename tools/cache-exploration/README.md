# Private actual-guest cache exploration controller

These host scripts prepare and control one allocated Linux n72 guest
exploration. They are diagnostic tools, not a shipping cache feature or a
firmware download/preparation route. Cache stays off. Actual Home/Settings
inspection and a fresh exact-packet Root grant are required; no frame counter
or image heuristic creates a scene pass. Preserve each failed packet/run.

Deploy the five Python sources alongside the privately supplied `PLAN.json`
and artifact/receipt paths. Run `prepare-exploration.py --lock-policy` with the
existing audited `Linux/scripts/lock-continuity.py`. Preparation inventories
the existing immutable base, copies only small writable state using independent
inodes, and records actual executable/dependency pins. It does not start a guest
or GUI. Never commit generated inputs, state, inventories or private captures.

`EXPLORATION-TOOLS.json` binds the resolved absolute Xvfb, xdotool and ffmpeg
executables. The controller and capture helper use these paths and verify their
hashes; service PATH is irrelevant. PNG conversion is a three-second bounded
host operation. Its success says nothing about the image's guest scene.

The first private packet failed because its capture converter used bare
`ffmpeg`, which was missing from systemd's PATH. All actual BMPs were retained;
Home129s expired and ordinary shutdown was clean. The correction keeps that
failure and requires a separately granted successor. It does not turn prior
BMPs or a successful host conversion test into guest acceptance.

Run host contracts with a real local ffmpeg available through the initial PATH,
or set `LT_CACHE_TEST_FFMPEG` to its exact executable path:

```sh
python3 tools/cache-exploration/test-exploration.py -v
```

The converter regression runs the actual executable with PATH nonexistent and
with a conflicting basename. The rest exercises filesystem inventories, PIDFD
retirement, strict clean-stop fields, finite cgroup/grant policy, actual88px
toolbar geometry and retained-image continuity. These are host contracts only.
Read the private packet's `RUN-EXPLORATION.md` for its allocation, invocation,
source manifest, operator capture/inspection steps and independent retirement.

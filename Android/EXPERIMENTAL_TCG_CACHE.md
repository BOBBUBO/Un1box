# Experimental frontend IR cache hooks

This source is preparation for controlled guest tests. Ordinary Linux launches
and Android workers default to cache off and clear inherited cache opt-ins.
There is no app UI, imported configuration setting or release-build selector.
No actual iOS/Android correctness, benefit or packaged-cache qualification is
established by these hooks or their host tests.

An Android debug-only `IEngine.debugSelectTcgIrCache(boolean)` call may record a
selection only on a fresh unused worker. It never edits environment variables
at selection time. The service serializes it with the first consumed Start;
native Start then selects the actual local record and configures the engine
environment before loading/starting it. A later selector is rejected. The
`experimentalTcgIrCacheRequested` status field is a request, not an engine hit
or capability claim. Release calls are unavailable.

Enabled test workers use only `<private library>/tcg-ir-cache/<local record>`.
Directories must be owned by the current user and mode0700; symlinks, unsafe
existing modes and invalid record identities fail without deleting records or
repairing permissions. Cache data is not imported, exported or shared between
records. Native buffers/addresses are never persisted by the engine's symbolic
IR format. Restart always means a fresh worker; native code is regenerated.

Linux `launcher/experimental_cache.py` supplies the equivalent environment and
private-directory helper for a controlled test harness. The ordinary launcher
always calls it with `False`; there is deliberately no CLI enablement yet.
The helper returns a copied environment, preserves app-service endpoints and
does not edit the UI process environment.

Host-only validation:

```sh
python3 -m unittest discover -s Linux/launcher/tests -p test_experimental_cache.py -v
c++ -std=c++17 -Wall -Wextra -Werror -UNDEBUG \
  Android/app/src/test/cpp/ExperimentalTcgCache.cpp -o /private/cache-contract
/private/cache-contract
```

The Kotlin admission test belongs to the existing app unit-test suite. Native
helper/JNI/Kotlin compilation must be recorded separately from Python tests;
none is an actual target guest result. Qualification requires the same engine,
frontend, immutable base and clean seed, isolated per-trial writable state,
cache-off/cold/reuse fresh processes, reviewed actual guest frames, invalidation
checks, clean shutdown, retained cache counters and measured timing/CPU effects.

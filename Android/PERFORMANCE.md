# Host performance panel (future producer)

The collapsed **Performance** button requests no samples. While the panel is open
and its Activity is started, a separate UI background reader requests a cached
snapshot each second. The worker admits at most one collection every two seconds,
with one in-flight read and no queued history. Closing/backgrounding the panel
stops requests; one already admitted read may finish. No timer continues sampling
in the worker. A stuck platform memory read cannot queue more reads or block a
native/control/audio lock. Worker destruction interrupts the sampler without
joining it.

The displayed values have distinct scopes:

| Label | Actual measurement |
| --- | --- |
| Emulator CPU | Whole `:engine` process CPU-time delta / elapsed-time delta, multiplied by 100. One busy core is 100%; multiple cores may exceed 100%. This includes all worker threads and sampler overhead. |
| Emulator memory | Current worker's reported proportional set size (PSS), converted from KiB to MiB. This is neither RSS nor guest RAM usage. Android may omit protected allocations such as graphics. |
| Android RAM | Device memory available / total memory accessible to the kernel, converted from bytes to MiB. Available is not identical to free memory. |
| Android whole-device CPU | Unavailable; no privileged or `/proc` fallback. |
| iOS guest CPU / RAM | Unavailable. The current guest protocol has no measured guest statistics operation. Configured guest RAM and host TCG CPU are not substitutes. |

The CPU counters are `Process.getElapsedCpuTime()` and
`SystemClock.elapsedRealtime()` milliseconds. Their interval includes Android
deep sleep. Guest Pause does **not** reset or force CPU to zero: host work can
continue while emulated execution is paused. A new PID, panel/foreground
observation epoch, invalid/regressing counter, nonpositive interval or gap above
10 seconds warms up a fresh baseline. A valid zero CPU delta is displayed as 0%.

Each metric includes its own elapsed-time sample/read-completion age and the
worker PID. Platform memory reads are not an atomic snapshot with CPU or each
other; the Bundle additionally retains collection completion/cost and raw units.
A slow read leaves prior values visible with increasing ages. Missing/failed
reads are unavailable, never silently substituted with zero. A nonpositive PSS
from a live worker is also unavailable: the public void memory wrapper does not
expose successful map reading and can leave a zero-initialized result unchanged.
This differs from a valid measured zero CPU delta. Six fixed-height,
single-line rows prevent sample updates from changing the viewport; opening or
closing the panel can resize it and retains existing gesture cancellation rules.

The single trailing `IEngine.performance(long observationEpoch)` method only
requests collection and returns scalar cached data. Its observer key is the
calling process PID plus an Activity-local token. This token provides **no**
display, input, guest or process-control authority. A changed observer or demand
gap above four seconds invalidates old cache publication and resets the CPU
sampling epoch. Late UI responses are accepted only for the same Binder and
current observation token. There are no changes to Native/status/observations,
Portable APIs, audio policy, settings or gyro support.

`PerformanceCountersTest` contains eleven source regression cases covering multicore
and zero usage, PID/epoch changes, invalid counters/clocks, long gaps without
invented guest-pause semantics, cadence/single-flight, observer handoff, blocked
read closure, duplicate completions, and an unfilled void-memory-read result. They are written but **not run** pending
the owner's compiler allocation. Main/AIDL/Compose and instrumentation compilation
and an actual target inspection are also pending. Sealed candidate 24 and its
24b helper runner are not modified or qualified by this source change.

Primary API definitions checked for this implementation:

- [Process CPU time](https://developer.android.com/reference/android/os/Process#getElapsedCpuTime())
- [Elapsed time, including deep sleep](https://developer.android.com/reference/android/os/SystemClock#elapsedRealtime())
- [Own-process memory information and protected-allocation limitation](https://developer.android.com/reference/android/os/Debug#getMemoryInfo(android.os.Debug.MemoryInfo))
- [PSS in kB](https://developer.android.com/reference/android/os/Debug.MemoryInfo#getTotalPss())
- [Available and total device memory](https://developer.android.com/reference/android/app/ActivityManager.MemoryInfo)

PSS failure semantics are grounded in the pinned Android 14 public
[void self-memory wrapper](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-14.0.0_r1/core/java/android/os/Debug.java)
and [native failed map-load branch](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-14.0.0_r1/core/jni/android_os_Debug.cpp).
The hidden PID overload's boolean result is not used. A positive-only live-worker
PSS check avoids interpreting an unfilled result as a successful zero measurement;
it does not diagnose the cause of an unavailable read.

#pragma once
#include <sys/resource.h>
#include <cstdlib>
#include <cstring>

// The presenter and the vsync looper are woken for a few hundred microseconds per frame and must
// not queue behind the engine's vCPU threads, which run at the app's default nice (ADPF raises
// them while the guest falls behind, but a frame handed over late is already late). Android's
// own display threads run at THREAD_PRIORITY_DISPLAY, nice -4.
//
// Best effort: a negative nice needs the process's RLIMIT_NICE to allow it, and where the platform
// refuses the call fails and nothing changes. LT_PRESENT_PRIO=off is the A/B arm.
inline void request_display_priority() {
    static const bool enabled = [] {
        const char *value = getenv("LT_PRESENT_PRIO");
        return !(value && (!strcmp(value, "off") || !strcmp(value, "0")));
    }();
    if (!enabled) return;
    setpriority(PRIO_PROCESS, 0, -4);   // the calling thread, not the process
}

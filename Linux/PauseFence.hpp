#pragma once
#include <cstdint>

namespace lt {
// VM pause/resume admission is asynchronous. Do not turn a quick resume key
// into a RUNNING acknowledgement of a pause the worker has not observed yet.
class PauseFence {
    bool waiting_for_pause = false, resume_pending = false;
    uint64_t pending_command = 0;

  public:
    void pause_admitted() {
        waiting_for_pause = true;
        resume_pending = false;
    }
    bool defer_resume(uint64_t command) {
        if (!waiting_for_pause)
            return false;
        resume_pending = true;
        pending_command = command;
        return true;
    }
    bool resume_ready(bool paused, bool stopping, uint64_t &command) {
        if (stopping) {
            resume_pending = false;
            return false;
        }
        if (paused)
            waiting_for_pause = false;
        if (waiting_for_pause || !resume_pending)
            return false;
        resume_pending = false;
        command = pending_command;
        return true;
    }
};
} // namespace lt

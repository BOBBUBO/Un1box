#pragma once
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dirent.h>
#include <sched.h>
#include <unistd.h>

// Big-core affinity for the guest's vCPU threads.
//
// ADPF (performance_hint.h) asks the platform for a clock and a core placement; it does
// not promise either. A phone's scheduler is free to leave a busy thread on a little
// core when something else wants the big ones -- and here that something else is our own
// work: the GL worker, the frame copier and the presenter all want cycles, and on a
// big.LITTLE part the vCPU is the thread that decides the frame rate. Pinning it to the
// cores the platform itself reports as the fast ones keeps it from being moved onto a
// little core mid-frame.
//
// Only the vCPU threads are pinned; everything else stays free to run anywhere,
// including those cores. When there is no capacity information (some devices expose
// none), or every core reports the same capacity, this does nothing -- a mask covering
// every core would be a no-op with a syscall attached. LT_VCPU_BIG_CORES=off for A/B.
class VcpuBigCoreAffinity {
public:
    // Called from the frame publisher thread, like the ADPF session.
    void framePublished() {
        if (state_ == State::off) return;
        if (frames_++ % 120) return;  // every two seconds at 60 Hz
        apply();
    }
private:
    enum class State { unknown, off, active };
    static constexpr int kMaxCpus = 32;   // far past any phone; CPU_SET stops earlier

    // Cores whose capacity is at least this share of the fastest one count as big.
    static constexpr int64_t kBigShareNum = 3, kBigShareDen = 4;

    static int64_t capacity(int cpu) {
        char path[96];
        long long value = -1;

        snprintf(path, sizeof path, "/sys/devices/system/cpu/cpu%d/cpu_capacity", cpu);
        if (FILE *f = fopen(path, "re")) {
            if (fscanf(f, "%lld", &value) != 1) value = -1;
            fclose(f);
        }
        return value;
    }

    // The mask of big cores, once. A mask covering every core, or none, is a no-op.
    void resolve() {
        static const char *mode = getenv("LT_VCPU_BIG_CORES");
        int64_t best = 0;

        if (mode && (!strcmp(mode, "off") || !strcmp(mode, "0"))) {
            state_ = State::off;
            return;
        }
        for (int cpu = 0; cpu < kMaxCpus; cpu++) {
            int64_t cap = capacity(cpu);
            if (cap > best) best = cap;
        }
        if (best <= 0) {  // this device reports no capacity at all
            state_ = State::off;
            return;
        }
        CPU_ZERO(&mask_);
        for (int cpu = 0; cpu < kMaxCpus && cpu < CPU_SETSIZE; cpu++) {
            int64_t cap = capacity(cpu);
            if (cap <= 0) continue;
            total_++;
            if (cap * kBigShareDen >= best * kBigShareNum) {
                CPU_SET(cpu, &mask_);
                cpus_++;
            }
        }
        if (cpus_ == 0 || cpus_ == total_) {
            state_ = State::off;  // every core qualifies: nothing to prefer
            return;
        }
        state_ = State::active;
        fprintf(stderr, "[perf] vCPU threads prefer %d of %d cores\n", cpus_, total_);
    }

    // Pin whatever vCPU threads exist now. Run again on later frames, because the engine
    // recreates them on a reset; an already-pinned thread just takes the same syscall.
    void apply() {
        if (state_ == State::unknown) resolve();
        if (state_ != State::active) return;
        DIR *tasks = opendir("/proc/self/task");
        if (!tasks) return;
        int pinned = 0;
        while (dirent *task = readdir(tasks)) {
            char path[300], name[32] = {};
            snprintf(path, sizeof path, "/proc/self/task/%s/comm", task->d_name);
            if (FILE *f = fopen(path, "re")) {
                if (!fgets(name, sizeof name, f)) name[0] = 0;
                fclose(f);
            }
            size_t n = strcspn(name, "\n");
            name[n] = 0;
            // QEMU names its vCPU threads "CPU 0/TCG" (see performance_hint.h).
            if (strncmp(name, "CPU ", 4) || n <= 4 || strcmp(name + n - 4, "/TCG")) continue;
            int tid = atoi(task->d_name);
            if (tid <= 0) continue;
            if (sched_setaffinity(tid, sizeof mask_, &mask_) != 0) {
                if (!refused_) {
                    refused_ = true;
                    fprintf(stderr, "[perf] sched_setaffinity refused: %s\n", strerror(errno));
                }
                continue;
            }
            pinned++;
        }
        closedir(tasks);
        if (pinned && !logged_) {
            logged_ = true;
            fprintf(stderr, "[perf] pinned %d vCPU thread(s) to the big cores\n", pinned);
        }
    }

    State state_ = State::unknown;
    cpu_set_t mask_{};
    int cpus_ = 0, total_ = 0;
    unsigned frames_ = 0;
    bool logged_ = false, refused_ = false;
};

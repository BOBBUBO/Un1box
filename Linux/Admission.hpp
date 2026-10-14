#pragma once
#include <cstdint>
#include <functional>
#include <stdexcept>
#include <string>
#include <utility>

namespace lt {
inline constexpr uint64_t admission_absolute_ns = 900000000000ull;
inline constexpr uint64_t admission_stall_ns = 30000000000ull;
inline constexpr uint64_t admission_cancel_grace_ns = 5000000000ull;
inline constexpr uint64_t admission_publish_ns = 250000000ull;
// Observer bytes do not expose Storage's internal stage names.
enum class AdmissionPhase : uint32_t { plan = 1, digest = 2, checks = 3, runtime_setup = 4 };
struct AdmissionProgress {
    uint64_t done = 0, total = 0, sequence = 0, worker_monotonic_ns = 0;
    AdmissionPhase phase = AdmissionPhase::plan;
};
inline const char *admission_phase_name(AdmissionPhase phase) {
    switch (phase) {
    case AdmissionPhase::plan:
        return "plan/checkpoints";
    case AdmissionPhase::digest:
        return "digest-bytes";
    case AdmissionPhase::checks:
        return "post-digest-checkpoints";
    case AdmissionPhase::runtime_setup:
        return "runtime-setup";
    }
    return "invalid";
}
inline bool admission_elapsed(uint64_t now, uint64_t since, uint64_t budget) {
    return now >= since && now - since >= budget;
}
class AdmissionWatch {
    uint64_t started_, checkpoint_;
    AdmissionProgress previous_{};

  public:
    explicit AdmissionWatch(uint64_t started) : started_(started), checkpoint_(started) {}
    void observe(const AdmissionProgress &p, uint64_t received) {
        const auto phase = uint32_t(p.phase);
        if (phase < 1 || phase > 4 || p.done > p.total || !p.sequence || p.sequence <= previous_.sequence ||
            received < checkpoint_ || p.worker_monotonic_ns < started_ || p.worker_monotonic_ns > received ||
            p.worker_monotonic_ns < previous_.worker_monotonic_ns || p.done < previous_.done ||
            (previous_.total && p.total != previous_.total) || phase < uint32_t(previous_.phase) ||
            (p.phase == AdmissionPhase::plan && (p.done || p.total)) ||
            (!p.total && p.phase != AdmissionPhase::plan && p.phase != AdmissionPhase::runtime_setup) ||
            (p.phase == AdmissionPhase::digest && p.done == p.total) ||
            (p.phase == AdmissionPhase::checks && p.done != p.total) ||
            (p.phase == AdmissionPhase::runtime_setup && p.done != p.total))
            throw std::runtime_error("invalid admission progress receipt");
        previous_ = p;
        checkpoint_ = received;
    }
    bool absolute_expired(uint64_t now) const { return admission_elapsed(now, started_, admission_absolute_ns); }
    bool stalled(uint64_t now) const { return admission_elapsed(now, checkpoint_, admission_stall_ns); }
    const AdmissionProgress &latest() const { return previous_; }
};
// No C ABI re-entry: these callbacks only inspect private IPC and publish receipts.
class AdmissionObserver {
    std::function<uint64_t()> clock_;
    std::function<bool()> cancel_;
    std::function<bool(const AdmissionProgress &)> publish_;
    uint64_t started_, sent_ = 0;
    AdmissionPhase sent_phase_ = AdmissionPhase::plan;

  public:
    AdmissionProgress progress{};
    bool cancelled = false;
    std::string reason;
    AdmissionObserver(std::function<uint64_t()> clock, std::function<bool()> cancel,
                      std::function<bool(const AdmissionProgress &)> publish)
        : clock_(std::move(clock)), cancel_(std::move(cancel)), publish_(std::move(publish)), started_(clock_()) {}
    int checkpoint(uint64_t done, uint64_t total, bool setup = false) {
        try {
            const uint64_t now = clock_();
            if (cancelled || cancel_()) {
                cancelled = true;
                if (reason.empty())
                    reason = "admission cancelled by parent";
                return 1;
            }
            if (admission_elapsed(now, started_, admission_absolute_ns)) {
                cancelled = true;
                reason = "admission absolute deadline exceeded";
                return 1;
            }
            const auto phase = setup          ? AdmissionPhase::runtime_setup
                               : !total       ? AdmissionPhase::plan
                               : done < total ? AdmissionPhase::digest
                                              : AdmissionPhase::checks;
            const bool completed = total && done == total && progress.done != done;
            progress.done = done;
            progress.total = total;
            progress.phase = phase;
            if (!progress.sequence || phase != sent_phase_ || completed ||
                admission_elapsed(now, sent_, admission_publish_ns)) {
                auto receipt = progress;
                receipt.sequence++;
                receipt.worker_monotonic_ns = now;
                if (publish_(receipt)) {
                    progress = receipt;
                    sent_ = now;
                    sent_phase_ = phase;
                }
            }
            return 0;
        } catch (const std::exception &e) {
            cancelled = true;
            reason = e.what();
            return 1;
        }
    }
    static int notify(void *opaque, uint64_t done, uint64_t total) {
        return static_cast<AdmissionObserver *>(opaque)->checkpoint(done, total);
    }
};
} // namespace lt

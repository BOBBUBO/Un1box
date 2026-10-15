#pragma once
#include <cstdint>
#include <limits>

namespace lt {
// Agent leases use host time and can expire while guest execution is paused.
// Poll from the publisher after resume/cancellation; never wait inside QEMU.
class GuestShutdownAdmission {
public:
    static constexpr uint64_t refresh_budget_ns = 5'000'000'000ull;
    void observe(uint32_t agent) { seen_ = seen_ || agent == 1 || agent == 2; }
    void request(uint64_t now) {
        if (pending_ || sent_) return;
        pending_ = true;
        deadline_ = now > std::numeric_limits<uint64_t>::max() - refresh_budget_ns
            ? std::numeric_limits<uint64_t>::max() : now + refresh_budget_ns;
    }
    bool pending() const { return pending_; }
    bool admit(uint32_t agent, uint64_t now) {
        observe(agent);
        if (!pending_ || sent_) return false;
        if (agent != 1 && seen_ && now < deadline_) return false;
        pending_ = false;
        sent_ = true;
        return true;
    }
private:
    bool seen_ = false, pending_ = false, sent_ = false;
    uint64_t deadline_ = 0;
};
}

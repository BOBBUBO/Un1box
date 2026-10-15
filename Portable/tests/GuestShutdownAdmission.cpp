#include "../src/GuestShutdownAdmission.hpp"
#include <cassert>
#include <limits>

int main() {
    using lt::GuestShutdownAdmission;
    constexpr uint64_t second = 1'000'000'000ull;
    // A paused guest's stale lease must not choose the broken fallback 10ms
    // after resume. Once the real daemon polls, submit exactly once.
    GuestShutdownAdmission paused;
    paused.observe(1); paused.observe(2); paused.request(100 * second);
    assert(!paused.admit(2, 100 * second + 10'000'000));
    assert(paused.pending());
    assert(paused.admit(1, 101 * second));
    assert(!paused.admit(1, 102 * second));
    paused.request(103 * second); assert(!paused.admit(1, 103 * second));

    // Cancelling an outstanding guest RPC can clear the agent claim. An alive
    // pre-cancel snapshot cannot authorize immediate fallback after that reset.
    GuestShutdownAdmission cancelled;
    cancelled.observe(1); cancelled.request(0);
    assert(!cancelled.admit(0, second));
    assert(cancelled.admit(1, 2 * second));

    // No agent has ever been present on older boards: keep immediate fallback.
    GuestShutdownAdmission absent;
    absent.observe(0); absent.request(7 * second);
    assert(absent.admit(0, 7 * second));

    // A missing daemon cannot postpone the original 35s disposal policy.
    // Repeated requests do not move the fixed five-second refresh limit.
    GuestShutdownAdmission lost;
    lost.observe(2); lost.request(10 * second); lost.request(14 * second);
    assert(!lost.admit(0, 15 * second - 1));
    assert(lost.admit(0, 15 * second));
    assert(!lost.admit(0, 16 * second));

    GuestShutdownAdmission overflow;
    const auto maximum = std::numeric_limits<uint64_t>::max();
    overflow.observe(2); overflow.request(maximum - 1);
    assert(!overflow.admit(2, maximum - 1));
    assert(overflow.admit(2, maximum));
}

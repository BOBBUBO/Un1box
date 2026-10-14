#include "../Admission.hpp"
#include "lighttouch.h"
#include <cassert>
#include <cerrno>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>
namespace fs = std::filesystem;
static void put(const fs::path &path, const std::string &value) {
    fs::create_directories(path.parent_path());
    std::ofstream file(path, std::ios::binary);
    file << value;
    assert(file.good());
}
static std::string hash(const fs::path &path) {
    char out[65];
    lt_error error{sizeof(error), 1, 0, {}};
    assert(lt_sha256_file(path.c_str(), out, &error) == LT_OK);
    return out;
}
int main() {
    // Fresh receipts cannot extend the absolute budget; no wall-clock sleep needed.
    lt::AdmissionWatch watch(1);
    for (uint64_t second = 1; second <= 900; second++) {
        lt::AdmissionProgress p{0, 0, second, second * 1000000000ull, lt::AdmissionPhase::plan};
        watch.observe(p, p.worker_monotonic_ns);
        assert(!watch.stalled(p.worker_monotonic_ns));
    }
    assert(watch.absolute_expired(lt::admission_absolute_ns + 1));
    assert(!watch.absolute_expired(0)); // An older loop snapshot never underflows.
    assert(watch.stalled(900000000000ull + lt::admission_stall_ns));
    bool rejected = false;
    try {
        watch.observe({1, 0, 901, 901000000000ull, lt::AdmissionPhase::plan}, 901000000000ull);
    } catch (const std::runtime_error &) {
        rejected = true;
    }
    assert(rejected);

    lt::AdmissionWatch ordered(1);
    ordered.observe({1, 10, 1, 2, lt::AdmissionPhase::digest}, 3);
    for (const lt::AdmissionProgress &invalid :
         {lt::AdmissionProgress{2, 11, 2, 4, lt::AdmissionPhase::digest},    // Fixed total changed.
          lt::AdmissionProgress{2, 10, 1, 4, lt::AdmissionPhase::digest},    // Duplicate sequence.
          lt::AdmissionProgress{0, 10, 2, 4, lt::AdmissionPhase::digest},    // Bytes moved backwards.
          lt::AdmissionProgress{2, 10, 2, 5, lt::AdmissionPhase::digest}}) { // Timestamp in future.
        rejected = false;
        try {
            ordered.observe(invalid, 4);
        } catch (const std::runtime_error &) {
            rejected = true;
        }
        assert(rejected);
    }

    char name[] = "/tmp/lighttouch-linux-admission-XXXXXX";
    assert(mkdtemp(name));
    const fs::path root(name), base = root / "base", state = root / "state";
    struct Cleanup {
        fs::path root;
        ~Cleanup() {
            for (const auto &entry : fs::recursive_directory_iterator(root))
                if (entry.is_directory())
                    chmod(entry.path().c_str(), 0700);
            fs::remove_all(root);
        }
    } cleanup{root};
    put(base / "nand/one.pages", std::string(131089, 'p'));
    put(base / "nor.bin", "nor");
    put(base / "iBoot.bin", "boot");
    put(base / "gid-blobs.bin", "gid");
    put(base / "identity.json", "{\"seed\":\"synthetic\"}");
    put(root / "listing", "one.pages " + hash(base / "nand/one.pages") + "\n");
    put(base / "device.lock.json",
        "{\"format\":1,\"board\":\"n72ap\",\"build\":\"7E18\",\"boot_strategy\":\"iboot\",\"outputs\":{"
        "\"nand\":{\"path\":\"nand\",\"listing_sha256\":\"" +
            hash(root / "listing") +
            "\",\"pages\":1},"
            "\"nor\":{\"path\":\"nor.bin\",\"sha256\":\"" +
            hash(base / "nor.bin") +
            "\"},"
            "\"iboot\":{\"path\":\"iBoot.bin\",\"sha256\":\"" +
            hash(base / "iBoot.bin") +
            "\"},"
            "\"gid_blobs\":{\"path\":\"gid-blobs.bin\",\"sha256\":\"" +
            hash(base / "gid-blobs.bin") +
            "\"}},"
            "\"identity\":{\"sha256\":\"" +
            hash(base / "identity.json") + "\"}}");
    for (const auto &entry : fs::recursive_directory_iterator(base))
        chmod(entry.path().c_str(), entry.is_directory() ? 0500 : 0400);
    chmod(base.c_str(), 0500);
    std::string base_path = base.string(), state_path = state.string();
    lt_device_config config{sizeof(config), 1, base_path.c_str(), state_path.c_str(), 0};
    lt_device *device = nullptr;
    lt_error error{sizeof(error), 1, 0, {}};
    int channels[2];
    assert(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_NONBLOCK | SOCK_CLOEXEC, 0, channels) == 0);
    struct Sockets {
        int *p;
        ~Sockets() {
            close(p[0]);
            close(p[1]);
        }
    } sockets{channels};
    uint64_t clock = 1;
    auto publish = [&](const lt::AdmissionProgress &p) {
        return send(channels[0], &p, sizeof(p), MSG_DONTWAIT | MSG_NOSIGNAL) == sizeof(p);
    };
    lt::AdmissionObserver observed(
        [&] {
            clock += 250000000ull;
            return clock;
        },
        [] { return false; }, publish);
    assert(lt_device_open_observed(&config, lt::AdmissionObserver::notify, &observed, &device, &error) == LT_OK);
    assert(device && !fs::exists(state / ".dirty"));
    lt_device_close(device);
    device = nullptr;
    lt::AdmissionWatch received(1);
    lt::AdmissionProgress packet;
    unsigned receipts = 0;
    while (recv(channels[1], &packet, sizeof(packet), MSG_DONTWAIT) == sizeof(packet)) {
        received.observe(packet, clock);
        receipts++;
    }
    assert(receipts >= 3 && received.latest().done == received.latest().total && received.latest().total > 131089);

    // A real control datagram cancels the next checked digest chunk, releasing the actual native lease.
    put(state / ".dirty", "retained interrupted session");
    config.allow_dirty = 1;
    lt::AdmissionObserver cancelled(
        [&] {
            clock += 250000000ull;
            return clock;
        },
        [&] {
            char request;
            return recv(channels[0], &request, 1, MSG_DONTWAIT) == 1;
        },
        [&](const lt::AdmissionProgress &p) {
            assert(device == nullptr);
            if (p.done >= 65536) {
                const char request = 'C';
                assert(send(channels[1], &request, 1, MSG_NOSIGNAL) == 1);
            }
            return publish(p);
        });
    assert(lt_device_open_observed(&config, lt::AdmissionObserver::notify, &cancelled, &device, &error) ==
           LT_CANCELLED);
    assert(device == nullptr && fs::exists(state / ".dirty"));
    assert(lt_device_open(&config, &device, &error) == LT_OK); // Lease was unwound, not a mock release.
    lt_device_close(device);
    device = nullptr;
    config.allow_dirty = 0;
    assert(lt_device_open(&config, &device, &error) == LT_DIRTY && device == nullptr);

    // Complete digest bytes are not verified admission: a corrupted final page still fails.
    chmod((base / "nand/one.pages").c_str(), 0600);
    {
        std::fstream tail(base / "nand/one.pages", std::ios::in | std::ios::out | std::ios::binary);
        tail.seekp(-1, std::ios::end);
        tail.put('x');
        assert(tail.good());
    }
    chmod((base / "nand/one.pages").c_str(), 0400);
    lt::AdmissionObserver corrupt(
        [&] {
            clock += 250000000ull;
            return clock;
        },
        [] { return false; }, publish);
    assert(lt_device_open_observed(&config, lt::AdmissionObserver::notify, &corrupt, &device, &error) == LT_INVALID);
    assert(device == nullptr && corrupt.progress.done == corrupt.progress.total && fs::exists(state / ".dirty"));

    // Publisher pressure drops observations, never blocks callbacks or fabricates sequence acknowledgments.
    while (send(channels[0], &packet, sizeof(packet), MSG_DONTWAIT | MSG_NOSIGNAL) == sizeof(packet)) {
    }
    assert(errno == EAGAIN || errno == EWOULDBLOCK);
    lt::AdmissionObserver pressure(
        [&] {
            clock += 250000000ull;
            return clock;
        },
        [] { return false; }, publish);
    assert(pressure.checkpoint(4, 10) == 0 && pressure.progress.sequence == 0);
    while (recv(channels[1], &packet, sizeof(packet), MSG_DONTWAIT) == sizeof(packet)) {
    }
    assert(pressure.checkpoint(5, 10) == 0 && pressure.progress.sequence == 1);
    assert(recv(channels[1], &packet, sizeof(packet), MSG_DONTWAIT) == sizeof(packet) && packet.done == 5);
    std::cout << "Linux observed admission/socket/cancel/deadline contracts PASS (synthetic; no engine)\n";
}

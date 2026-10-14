#include "lighttouch.h"
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <sys/stat.h>
#include <utility>
#include <vector>

namespace fs = std::filesystem;
static void put(const fs::path &path, const std::string &bytes) {
    fs::create_directories(path.parent_path());
    std::ofstream file(path, std::ios::binary | std::ios::trunc);
    file.write(bytes.data(), bytes.size());
    assert(file.good());
}
static std::string digest(const fs::path &path) {
    lt_error error{sizeof(error), 1, 0, {}};
    char value[65]{};
    assert(lt_sha256_file(path.c_str(), value, &error) == LT_OK);
    return value;
}
struct Observation {
    enum Cancel { never, initial, hash_chunk, between_files, completed_bytes, after_hashing } cancel = never;
    lt_device **output = nullptr;
    std::vector<std::pair<uint64_t, uint64_t>> calls;
    static int notify(void *opaque, uint64_t done, uint64_t total) {
        auto &self = *static_cast<Observation *>(opaque);
        assert(self.output && *self.output == nullptr);
        assert(done <= total);
        if (!self.calls.empty()) {
            assert(done >= self.calls.back().first && total >= self.calls.back().second);
            assert(done - self.calls.back().first <= 65536);
            if (self.calls.back().second)
                assert(total == self.calls.back().second);
        }
        const bool same = !self.calls.empty() && done && done == self.calls.back().first;
        self.calls.emplace_back(done, total);
        return self.cancel == initial || (self.cancel == hash_chunk && done >= 65536) ||
               (self.cancel == between_files && same && done < total) ||
               (self.cancel == completed_bytes && total && done == total) ||
               (self.cancel == after_hashing && total && done == total && same);
    }
    void complete(uint64_t total) const {
        assert(!calls.empty() && calls.front() == std::make_pair(uint64_t(0), uint64_t(0)));
        assert(calls.back() == std::make_pair(total, total));
    }
};
int main() {
    char temporary[] = "/tmp/lighttouch-observed-open-XXXXXX";
    assert(mkdtemp(temporary));
    const fs::path root(temporary), base = root / "base";
    struct Cleanup {
        fs::path root;
        ~Cleanup() {
            for (const auto &entry : fs::recursive_directory_iterator(root))
                if (entry.is_directory())
                    chmod(entry.path().c_str(), 0700);
            fs::remove_all(root);
        }
    } cleanup{root};
    const std::string page_a(131089, 'a'), page_z(65553, 'z'), nor = "nor", iboot = "boot", gid = "gid";
    const std::string identity = "{\"seed\":\"synthetic\"}";
    put(base / "nand/a.pages", page_a);
    put(base / "nand/z.pages", page_z);
    put(base / "nor.bin", nor);
    put(base / "iBoot.bin", iboot);
    put(base / "gid-blobs.bin", gid);
    put(base / "identity.json", identity);
    const std::string listing =
        "a.pages " + digest(base / "nand/a.pages") + "\nz.pages " + digest(base / "nand/z.pages") + "\n";
    put(root / "listing", listing);
    put(base / "device.lock.json",
        "{\"format\":1,\"board\":\"n72ap\",\"build\":\"7E18\",\"boot_strategy\":\"iboot\",\"outputs\":{"
        "\"nand\":{\"path\":\"nand\",\"listing_sha256\":\"" +
            digest(root / "listing") +
            "\",\"pages\":2},"
            "\"nor\":{\"path\":\"nor.bin\",\"sha256\":\"" +
            digest(base / "nor.bin") +
            "\"},"
            "\"iboot\":{\"path\":\"iBoot.bin\",\"sha256\":\"" +
            digest(base / "iBoot.bin") +
            "\"},"
            "\"gid_blobs\":{\"path\":\"gid-blobs.bin\",\"sha256\":\"" +
            digest(base / "gid-blobs.bin") +
            "\"}},"
            "\"identity\":{\"sha256\":\"" +
            digest(base / "identity.json") + "\"}}");
    for (const auto &entry : fs::recursive_directory_iterator(base))
        assert(chmod(entry.path().c_str(), entry.is_directory() ? 0500 : 0400) == 0);
    assert(chmod(base.c_str(), 0500) == 0);
    const uint64_t total = page_a.size() + page_z.size() + nor.size() + iboot.size() + gid.size() + identity.size();
    const std::string base_path = base.string();
    lt_error error{sizeof(error), 1, 0, {}};
    lt_device *device = nullptr;
    for (auto cancel : {Observation::initial, Observation::hash_chunk, Observation::between_files,
                        Observation::completed_bytes, Observation::after_hashing}) {
        const auto state = root / ("cancel-" + std::to_string(int(cancel)));
        const std::string state_path = state.string();
        lt_device_config config{sizeof(config), 1, base_path.c_str(), state_path.c_str(), 0};
        Observation observation{cancel, &device, {}};
        assert(lt_device_open_observed(&config, Observation::notify, &observation, &device, &error) == LT_CANCELLED);
        assert(device == nullptr && error.code == LT_CANCELLED && !fs::exists(state / ".dirty"));
        assert(!fs::exists(state / ".base-identity"));
        // Actual reopen succeeds after cancellation: the writer lease unwound.
        assert(lt_device_open_observed(&config, nullptr, nullptr, &device, &error) == LT_OK);
        lt_device_close(device);
        device = nullptr;
    }
    const auto state = root / "success";
    const std::string state_path = state.string();
    lt_device_config config{sizeof(config), 1, base_path.c_str(), state_path.c_str(), 0};
    Observation observation{Observation::never, &device, {}};
    assert(lt_device_open_observed(&config, Observation::notify, &observation, &device, &error) == LT_OK);
    observation.complete(total);
    lt_device_info observed{sizeof(observed), 1, {}, {}, {}, {}, 0}, plain{sizeof(plain), 1, {}, {}, {}, {}, 0};
    assert(lt_device_describe(device, &observed, &error) == LT_OK);
    lt_device *blocked = nullptr;
    assert(lt_device_open(&config, &blocked, &error) == LT_BUSY && blocked == nullptr);
    lt_device_close(device);
    device = nullptr;
    assert(lt_device_open(&config, &device, &error) == LT_OK);
    assert(lt_device_describe(device, &plain, &error) == LT_OK);
    assert(std::strcmp(observed.base_sha256, plain.base_sha256) == 0 && observed.dirty == plain.dirty);
    lt_device_close(device);
    device = nullptr;
    put(state / ".dirty", "retained failure");
    config.allow_dirty = 1;
    observation = {Observation::hash_chunk, &device, {}};
    assert(lt_device_open_observed(&config, Observation::notify, &observation, &device, &error) == LT_CANCELLED);
    assert(device == nullptr && fs::exists(state / ".dirty"));
    config.allow_dirty = 0;
    assert(lt_device_open_observed(&config, nullptr, nullptr, &device, &error) == LT_DIRTY);

    // Record assets participate in the same fixed total, not a second epoch.
    const auto rom = root / "rom", package = root / "package";
    put(rom, std::string(70003, 'r'));
    put(package, std::string(70007, 'p'));
    const std::string library_path = (root / "library").string(), rom_path = rom.string(),
                      package_path = package.string();
    lt_library_config library_config{sizeof(library_config), 1, library_path.c_str()};
    lt_library *library = nullptr;
    assert(lt_library_open(&library_config, &library, &error) == LT_OK);
    lt_library_import_config imported{
        sizeof(imported),     1, base_path.c_str(), nullptr, "Synthetic", rom_path.c_str(),
        package_path.c_str(), 0, nullptr,           nullptr};
    lt_record_info record{sizeof(record), 1, {}, {}, {}, {}, {}, {}, {}, {}, {}, 0, 0};
    assert(lt_library_import(library, &imported, &record, &error) == LT_OK);
    observation = {Observation::hash_chunk, &device, {}};
    assert(lt_library_device_open_observed(library, record.id, 0, Observation::notify, &observation, &device, &error) ==
           LT_CANCELLED);
    assert(device == nullptr && !fs::exists(fs::path(record.state_path) / ".dirty"));
    observation = {Observation::never, &device, {}};
    assert(lt_library_device_open_observed(library, record.id, 0, Observation::notify, &observation, &device, &error) ==
           LT_OK);
    observation.complete(total + 70003 + 70007);
    lt_device_close(device);
    device = nullptr;
    assert(lt_library_device_open(library, record.id, 0, &device, &error) == LT_OK);
    lt_device_close(device);
    device = nullptr;
    assert(chmod(record.bootrom_path, 0600) == 0);
    auto bad_rom = std::string(70003, 'r');
    bad_rom.back() = '!';
    put(record.bootrom_path, bad_rom);
    assert(chmod(record.bootrom_path, 0400) == 0);
    observation = {Observation::never, &device, {}};
    assert(lt_library_device_open_observed(library, record.id, 0, Observation::notify, &observation, &device, &error) ==
           LT_INVALID);
    assert(device == nullptr && std::strstr(error.message, "ROM checksum mismatch"));
    observation.complete(total + 70003 + 70007);
    lt_library_close(library);

    // Every byte may be reported without admission succeeding: a changed tail
    // still fails the full authoritative NAND listing, with no published handle.
    assert(chmod((base / "nand/z.pages").c_str(), 0600) == 0);
    auto damaged = page_z;
    damaged.back() = '!';
    put(base / "nand/z.pages", damaged);
    assert(chmod((base / "nand/z.pages").c_str(), 0400) == 0);
    observation = {Observation::never, &device, {}};
    assert(lt_device_open_observed(&config, Observation::notify, &observation, &device, &error) == LT_INVALID);
    assert(device == nullptr && fs::exists(state / ".dirty"));
    observation.complete(total);
    std::cout << "Observed admission/cancel/lease/dirty/integrity contracts PASS (synthetic; no engine)\n";
}

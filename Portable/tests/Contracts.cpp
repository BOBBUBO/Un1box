#include "lighttouch.h"
#include "../src/Sha256.hpp"
#include "../src/Json.hpp"
#include <zlib.h>
#include <cassert>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <unistd.h>
#include <sys/stat.h>
namespace fs = std::filesystem;
static void put(const fs::path &p, const std::string &s) {
    fs::create_directories(p.parent_path());
    std::ofstream f(p, std::ios::binary);
    f.write(s.data(), s.size());
    assert(f.good());
}
static std::string hash(const std::string &s) {
    lt::Sha256 h;
    h.update(s.data(), s.size());
    return h.finish();
}
static void archive(const fs::path &path, const std::string &index, const std::string &data,
                    const std::string &tail = "") {
    std::string blob = "ITPACK01";
    uint32_t n = uint32_t(index.size());
    for (int i = 0; i < 4; i++)
        blob += char(n >> (i * 8));
    blob += index;
    uLongf cap = compressBound(data.size());
    std::string stream(cap, '\0');
    assert(compress2(reinterpret_cast<Bytef *>(stream.data()), &cap, reinterpret_cast<const Bytef *>(data.data()),
                     data.size(), 6) == Z_OK);
    stream.resize(cap);
    put(path, blob + stream + tail);
}
static std::string sha_file(const fs::path &p) {
    char h[65];
    lt_error e{sizeof(e), 1, 0, {}};
    assert(lt_sha256_file(p.c_str(), h, &e) == LT_OK);
    return h;
}
int main() {
    char temp[] = "/tmp/lighttouch-contract-XXXXXX";
    assert(mkdtemp(temp));
    fs::path root = temp;
    lt_error e{sizeof(e), 1, 0, {}};
    assert(hash("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    assert(hash("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    assert(hash(std::string(1000000, 'a')) == "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");
    bool bad = false;
    try {
        lt::parse("{\"x\":1,\"x\":2}");
    } catch (...) {
        bad = true;
    }
    assert(bad);
    bad = false;
    try {
        lt::parse("{\"x\":18446744073709551616}").at("x").integer();
    } catch (...) {
        bad = true;
    }
    assert(bad);
    double x, y;
    assert(lt_map_contact(20, 30, 10, 10, 20, 40, 90, &x, &y) == LT_OK && x == .5 && y == .5);
    assert(lt_map_contact(0, 0, 10, 10, 20, 40, 0, &x, &y) == LT_AGAIN);
    assert(lt_map_contact(NAN, 0, 0, 0, 20, 40, 0, &x, &y) == LT_INVALID);
    lt_motion raw{sizeof(raw), 1, 0, 3, 0, {1, 2, 3}, {4, 5, 6}}, mapped{sizeof(mapped), 1, 0, 0, 0, {}, {}};
    assert(lt_map_motion(90, 900000000, 1000000000, &raw, &mapped, &e) == LT_OK);
    assert(mapped.acceleration[0] == -2 && mapped.acceleration[1] == 1 && mapped.angular_velocity[0] == -5);
    assert(lt_map_motion(0, 1, 1000000000, &raw, &mapped, &e) == LT_AGAIN);
    const std::string nor = "nor", page = "page", iboot = "boot", gid = "gid";
    std::string listing = "cs0/0.page " + hash(page) + "\n";
    std::string lock = "{\"format\":1,\"board\":\"n72ap\",\"build\":\"7E18\",\"boot_strategy\":\"iboot\",\"outputs\":{"
                       "\"nor\":{\"path\":\"nor.bin\",\"sha256\":\"" +
                       hash(nor) + "\"},\"nand\":{\"path\":\"nand\",\"listing_sha256\":\"" + hash(listing) +
                       "\",\"pages\":1}},\"machine\":{\"ecid\":\"0x123\"}}";
    std::vector<std::pair<std::string, std::string>> entries = {{"device.lock.json", lock},
                                                                {"nor.bin", nor},
                                                                {"nand/cs0/0.page", page},
                                                                {"iBoot.bin", iboot},
                                                                {"gid-blobs.bin", gid}};
    std::string index = "{\"entries\":[", data;
    for (size_t i = 0; i < entries.size(); i++) {
        if (i)
            index += ",";
        index += "{\"name\":\"" + entries[i].first + "\",\"size\":" + std::to_string(entries[i].second.size()) +
                 ",\"mode\":420}";
        data += entries[i].second;
    }
    index += "]}";
    archive(root / "base.itbase", index, data);
    std::string sha = sha_file(root / "base.itbase"), dest = (root / "base").string();
    lt_import_config config{sizeof(config), 1,      (root / "base.itbase").c_str(), dest.c_str(), sha.c_str(), 0,
                            nullptr,        nullptr};
    std::string source = (root / "base.itbase").string();
    config.source = source.c_str();
    assert(lt_import_archive(&config, &e) == LT_OK);
    assert((fs::status(root / "base/nor.bin").permissions() & fs::perms::owner_write) == fs::perms::none);
    std::string state = (root / "state").string();
    lt_device_config dc{sizeof(dc), 1, dest.c_str(), state.c_str(), 0};
    lt_device *d = nullptr, *second = nullptr;
    assert(lt_device_open(&dc, &d, &e) == LT_OK);
    assert(lt_device_open(&dc, &second, &e) == LT_BUSY);
    put(root / "rom", "rom");
    std::string rom = (root / "rom").string();
    lt_boot_config boot{sizeof(boot), 1, "iPod-Touch-2G", rom.c_str(), nullptr, nullptr, 0};
    int argc;
    const char *const *argv;
    assert(lt_device_boot_argv(d, &boot, &argc, &argv, &e) == LT_OK);
    std::string args = argv[2];
    assert(args.find("nandrw=" + state + "/nand") != std::string::npos && args.find("ecid=0x123") != std::string::npos);
    lt_device_close(d);
    put(root / "state/.dirty", "crash");
    assert(lt_device_open(&dc, &d, &e) == LT_DIRTY);
    dc.allow_dirty = 1;
    assert(lt_device_open(&dc, &d, &e) == LT_OK);
    assert(fs::exists(root / "state/.dirty"));
    lt_device_close(d);

    // Prepared n81 uses kboot, full NAND/NOR and the guest package family. No
    // independent ROM or n72 memory setting may leak into its recipe.
    std::string n81lock = "{\"format\":1,\"board\":\"n81ap\",\"build\":\"8C148\",\"boot_strategy\":\"kboot\","
                          "\"outputs\":{\"nor\":{\"path\":\"nor.bin\",\"sha256\":\"" +
                          hash(nor) + "\"},\"nand\":{\"path\":\"nand\",\"listing_sha256\":\"" + hash(listing) +
                          "\",\"pages\":1},\"kboot\":{\"path\":\"kboot.bin\",\"sha256\":\"" + hash(iboot) + "\"}}}";
    put(root / "n81source/device.lock.json", n81lock);
    put(root / "n81source/nand/cs0/0.page", page);
    put(root / "n81source/nor.bin", nor);
    put(root / "n81source/kboot.bin", iboot);
    std::string n81source = (root / "n81source").string(), n81base = (root / "n81base").string(),
                n81state = (root / "n81state").string();
    lt_import_config n81import{sizeof(n81import), 1, n81source.c_str(), n81base.c_str(), nullptr, 0, nullptr, nullptr};
    assert(lt_import_directory(&n81import, &e) == LT_OK);
    lt_device_config n81dc{sizeof(n81dc), 1, n81base.c_str(), n81state.c_str(), 0};
    assert(lt_device_open(&n81dc, &d, &e) == LT_OK);
    std::string manifest = "{\"format\":1,\"serial\":26,\"version\":\"1.1\",\"requires\":{\"boards\":[\"n81ap\"],"
                           "\"builds\":[\"8*\"],\"host\":{\"guest-package\":[1,1]}},\"hooks\":[],\"jobs\":[],\"files\":"
                           "[{\"name\":\"bin/tool\",\"mode\":\"755\",\"size\":4,\"sha256\":\"" +
                           hash("tool") + "\"}]}";
    std::string packindex =
        "{\"entries\":[{\"name\":\"k48-ios4/manifest.json\",\"size\":" + std::to_string(manifest.size()) +
        "},{\"name\":\"k48-ios4/bin/tool\",\"size\":4}]}";
    archive(root / "synthetic.itpack", packindex, manifest + "tool");
    std::string packpath = (root / "synthetic.itpack").string();
    lt_boot_config n81boot{sizeof(n81boot), 1, "iPod-Touch-4G", nullptr, packpath.c_str(), nullptr, 0};
    assert(lt_device_boot_argv(d, &n81boot, &argc, &argv, &e) == LT_OK);
    args = argv[2];
    assert(args.find("kboot=" + n81base + "/kboot.bin") != std::string::npos);
    assert(args.find("nand-overlay=" + n81state + "/nand") != std::string::npos);
    assert(args.find("nor-rw=" + n81state + "/nor.bin") != std::string::npos);
    assert(args.find("bootrom=") == std::string::npos);
    for (int i = 0; i < argc; i++)
        assert(std::string(argv[i]) != "-m");
    assert(fs::exists(root / "n81state/work/guest-offer/bin/tool"));
    lt_device_close(d);
    archive(root / "bad.itbase", "{\"entries\":[{\"name\":\"../escape\",\"size\":1,\"mode\":420}]}", "a");
    source = (root / "bad.itbase").string();
    sha = sha_file(source);
    dest = (root / "bad").string();
    config.source = source.c_str();
    config.destination = dest.c_str();
    config.expected_sha256 = sha.c_str();
    assert(lt_import_archive(&config, &e) == LT_INVALID && !fs::exists(dest) && !fs::exists(root / "escape"));
    archive(root / "tail.itbase", index, data, "bad");
    source = (root / "tail.itbase").string();
    sha = sha_file(source);
    dest = (root / "tail").string();
    config.source = source.c_str();
    config.destination = dest.c_str();
    config.expected_sha256 = sha.c_str();
    assert(lt_import_archive(&config, &e) == LT_INVALID && !fs::exists(dest));
    archive(root / "cancel.itbase", index, data);
    source = (root / "cancel.itbase").string();
    sha = sha_file(source);
    dest = (root / "cancel").string();
    config.source = source.c_str();
    config.destination = dest.c_str();
    config.expected_sha256 = sha.c_str();
    config.progress = [](void *, uint64_t, uint64_t) { return 1; };
    assert(lt_import_archive(&config, &e) == LT_CANCELLED && !fs::exists(dest));
    // A modified page invalidates the lock's full listing even when the lock file
    // itself still matches the device pin. Recover never silently accepts mutation.
    chmod((root / "base/nand/cs0/0.page").c_str(), 0600);
    put(root / "base/nand/cs0/0.page", "BAD!");
    chmod((root / "base/nand/cs0/0.page").c_str(), 0400);
    dc.base_path = (root / "base").c_str();
    dest = (root / "base").string();
    dc.base_path = dest.c_str();
    assert(lt_device_open(&dc, &d, &e) == LT_INVALID);
    for (const auto &entry : fs::recursive_directory_iterator(root))
        if (entry.is_directory())
            chmod(entry.path().c_str(), 0700);
    fs::remove_all(root);
    std::cout << "synthetic storage/ABI contracts passed (no guest boot claim)\n";
}

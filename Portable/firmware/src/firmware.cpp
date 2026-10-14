#include "internal.hpp"
#include <atomic>
#include <cerrno>
#include <cstring>
#include <ctime>
#include <fcntl.h>
#include <fstream>
#include <functional>
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <set>
#include <sstream>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <zlib.h>
extern "C" {
#include "unzip.h"
#include <dmg/dmg.h>
#include <dmg/filevault.h>
#include "CActivation.h"
int lt_fw_guard_hfs(int, char **);
int lt_fw_guard_dmg(const char *, const char *, const char *);
extern char endianness;
int lt_fw_xpwn_poisoned(void);
}
namespace ltf {
static std::atomic_flag busy = ATOMIC_FLAG_INIT;
static bool safe(const std::string &n) {
    if (n.empty() || n[0] == '/' || n.find('\\') != std::string::npos || n.find('\0') != std::string::npos)
        return false;
    for (auto &p : fs::path(n))
        if (p == ".." || p == ".")
            return false;
    return true;
}
struct Zip {
    unzFile z = nullptr;
    std::map<std::string, unz_file_info> members;
    explicit Zip(const fs::path &p) {
        z = unzOpen(p.c_str());
        require(z, "not readable ZIP");
        try {
            int result = unzGoToFirstFile(z);
            for (; result == UNZ_OK; result = unzGoToNextFile(z)) {
                unz_file_info i{};
                char name[4096];
                require(unzGetCurrentFileInfo(z, &i, name, sizeof(name), nullptr, 0, nullptr, 0) == UNZ_OK &&
                            i.size_filename < sizeof(name) && std::strlen(name) == i.size_filename,
                        "ZIP name bounds");
                std::string s = name;
                require(safe(s) && members.emplace(s, i).second && members.size() <= 100000,
                        "unsafe/duplicate ZIP member");
                require(!(i.flag & 1), "encrypted ZIP member");
            }
            require(result == UNZ_END_OF_LIST_OF_FILE, "corrupt ZIP directory traversal");
        } catch (...) {
            unzClose(z);
            z = nullptr;
            throw;
        }
    }
    ~Zip() {
        if (z)
            unzClose(z);
    }
    std::string basename(const std::string &name) {
        std::string found;
        for (auto &m : members)
            if (fs::path(m.first).filename() == name) {
                require(found.empty(), "ambiguous member " + name);
                found = m.first;
            }
        require(!found.empty(), "missing member " + name);
        return found;
    }
    Bytes get(const std::string &n, size_t limit = 128 * 1024 * 1024) {
        auto i = members.at(n);
        require(i.uncompressed_size <= limit, "ZIP member size");
        require(unzLocateFile(z, n.c_str(), 1) == UNZ_OK && unzOpenCurrentFile(z) == UNZ_OK, "ZIP member open");
        Bytes b(i.uncompressed_size);
        size_t off = 0;
        while (off < b.size()) {
            int k = unzReadCurrentFile(z, b.data() + off, std::min<size_t>(b.size() - off, 65536));
            if (k <= 0) {
                unzCloseCurrentFile(z);
                throw Error(LT_FW_FORMAT, "truncated ZIP member");
            }
            off += k;
        }
        require(unzCloseCurrentFile(z) == UNZ_OK, "ZIP CRC mismatch");
        return b;
    }
};
static void progress(const lt_fw_request &r, const char *s, uint64_t d = 0, uint64_t t = 0) {
    if (r.progress && r.progress(r.user, s, d, t))
        throw Error(LT_FW_CANCELLED, "preparation cancelled");
}
static std::string hash_file(const fs::path &p, const lt_fw_request &r, bool sha1 = false) {
    std::ifstream f(p, std::ios::binary);
    if (!f)
        throw Error(LT_FW_IO, "missing source " + p.string());
    EVP_MD_CTX *c = EVP_MD_CTX_new();
    require(c, "digest allocation");
    EVP_DigestInit_ex(c, sha1 ? EVP_sha1() : EVP_sha256(), nullptr);
    Bytes b(65536);
    uint64_t done = 0;
    try {
        while (f) {
            f.read((char *)b.data(), b.size());
            auto n = f.gcount();
            if (n) {
                EVP_DigestUpdate(c, b.data(), n);
                done += n;
                progress(r, "verify", done, 0);
            }
        }
        require(f.eof(), "hash read failed");
        unsigned char h[64];
        unsigned n = 0;
        EVP_DigestFinal_ex(c, h, &n);
        EVP_MD_CTX_free(c);
        std::string out;
        for (unsigned i = 0; i < n; i++) {
            out += "0123456789abcdef"[h[i] >> 4];
            out += "0123456789abcdef"[h[i] & 15];
        }
        return out;
    } catch (...) {
        EVP_MD_CTX_free(c);
        throw;
    }
}
static Json identity() {
    Bytes h(32);
    require(RAND_bytes(h.data(), h.size()) == 1, "random identity");
    const char *chars = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ";
    std::string serial;
    for (int i = 0; i < 11; i++)
        serial += chars[h[i] % 32];
    auto mac = [&](int last) {
        char s[18];
        snprintf(s, sizeof(s), "02:%02x:%02x:%02x:%02x:%02x", h[28], h[29], h[30], h[31], last);
        return std::string(s);
    };
    std::string wifi = mac(h[27] & 254), bt = mac((h[27] & 254) + 1), battery;
    for (int i = 0; i < 12; i++)
        battery += char('0' + h[i] % 10);
    char ecid[16];
    snprintf(ecid, sizeof(ecid), "0x%010llx", (unsigned long long)((number(h, 20, 5, true) & 0xffffffffffULL) | 1));
    return {{"seed", "portable-" + digest(h)},
            {"serial-number", serial},
            {"battery-serial", battery},
            {"wifi-mac", wifi},
            {"bt-mac", bt},
            {"model-number", "MB528"},
            {"region-info", "LL/A"},
            {"unique-chip-id", ecid},
            {"udid", digest(bytes(serial + wifi + bt), true)}};
}
struct Volume {
    fs::path image, work;
    const lt_fw_request &r;
    void cmd(std::vector<std::string> a) {
        progress(r, "filesystem");
        a.insert(a.begin(), "lt-fw-hfs");
        a.insert(a.begin() + 1, image.string());
        std::vector<char *> v;
        for (auto &s : a)
            v.push_back(s.data());
        require(lt_fw_guard_hfs(v.size(), v.data()) == 0, "HFS operation failed: " + a[2] + " " + a[3]);
    }
    Bytes get(const std::string &p) {
        auto out = work / "read";
        cmd({"get", p, out.string()});
        return read(out);
    }
    bool exists(const std::string &p) {
        try {
            get(p);
            return true;
        } catch (const Error &e) {
            if (e.status == LT_FW_CANCELLED)
                throw;
            return false;
        }
    }
    void putfile(const std::string &p, const Bytes &b, const std::string &mode = "644", int uid = 0) {
        require(p.size() > 1 && p[0] == '/' && safe(p.substr(1)), "invalid HFS target");
        std::string path;
        auto parts = fs::path(p).relative_path();
        for (auto it = parts.begin(); it != parts.end(); ++it) {
            auto next = it;
            ++next;
            if (next == parts.end())
                break;
            path += "/" + it->string();
            cmd({"mkdir", path});
        }
        auto payload = work / "payload";
        write(payload, b);
        cmd({"put", p, payload.string(), mode, std::to_string(uid)});
        require(digest(get(p)) == digest(b), "HFS write checksum " + p);
    }
    void edit(const std::string &p, std::function<void(Json &)> fn) {
        auto j = plist(get(p));
        require(j.is_object(), "system plist dictionary");
        fn(j);
        putfile(p, xml(j));
    }
};
static std::map<std::string, Bytes> unpack(const Bytes &b) {
    require(b.size() >= 14 && std::string(b.begin(), b.begin() + 8) == "ITPACK01", "invalid itpack");
    size_t n = number(b, 8, 4);
    require(n <= b.size() - 14 && n <= 8 * 1024 * 1024, "itpack index size");
    auto j = Json::parse(b.begin() + 12, b.begin() + 12 + n);
    uint64_t total = 0;
    std::set<std::string> names;
    for (auto &e : j.at("entries")) {
        std::string name = e.at("name");
        require(safe(name) && names.insert(name).second, "itpack name");
        uint64_t size = e.at("size").get<uint64_t>();
        require(size <= 128 * 1024 * 1024 && total + size <= 512 * 1024 * 1024, "itpack extent");
        total += size;
    }
    Bytes out(total);
    uLongf len = total;
    require(uncompress(out.data(), &len, b.data() + 12 + n, b.size() - 12 - n) == Z_OK && len == total,
            "itpack deflate/checksum");
    std::map<std::string, Bytes> entries;
    size_t off = 0;
    for (auto &e : j.at("entries")) {
        size_t size = e.at("size");
        entries[e.at("name").get<std::string>()] = Bytes(out.begin() + off, out.begin() + off + size);
        off += size;
    }
    return entries;
}
static void guest_macho(const Bytes &b) {
    if (b.size() >= 8 && number(b, 0, 4, true) == 0xcafebabe) {
        size_t n = number(b, 4, 4, true);
        require(n >= 1 && n <= 16 && n <= (b.size() - 8) / 20, "fat guest helper table");
        Bytes selected;
        std::vector<std::pair<size_t, size_t>> extents;
        for (size_t i = 0; i < n; i++) {
            size_t p = 8 + i * 20, offset = number(b, p + 8, 4, true), size = number(b, p + 12, 4, true),
                   align = number(b, p + 16, 4, true);
            require(offset >= 8 + n * 20 && offset <= b.size() && size >= 28 && size <= b.size() - offset &&
                        align <= 30 && !(offset & ((size_t(1) << align) - 1)),
                    "fat guest slice bounds");
            for (auto &e : extents)
                require(offset >= e.first + e.second || e.first >= offset + size, "overlapping fat guest slices");
            extents.emplace_back(offset, size);
            uint64_t sub = number(b, p + 4, 4, true) & 0xffffff;
            if (number(b, p, 4, true) == 12 && (sub == 6 || sub == 0)) {
                require(selected.empty(), "ambiguous ARMv6 guest slice");
                selected.assign(b.begin() + offset, b.begin() + offset + size);
            }
        }
        require(!selected.empty(), "missing ARMv6 slice");
        guest_macho(selected);
        return;
    }
    require(b.size() >= 28 && number(b, 0, 4) == 0xfeedface && number(b, 4, 4) == 12 &&
                ((number(b, 8, 4) & 0xffffff) == 6 || (number(b, 8, 4) & 0xffffff) == 0),
            "guest helper is not ARMv6 Mach-O");
    size_t n = number(b, 16, 4), sz = number(b, 20, 4), p = 28;
    require(sz <= b.size() - 28 && n <= sz / 8, "guest helper load bounds");
    for (size_t i = 0; i < n; i++) {
        require(p <= 28 + sz - 8, "guest helper command header");
        size_t len = number(b, p + 4, 4);
        require(len >= 8 && len <= 28 + sz - p, "guest helper command extent");
        p += len;
    }
}
static Json seed(Volume &v, const Bytes &packed) {
    auto e = unpack(packed);
    Json man;
    std::string family;
    for (auto &a : e)
        if (a.first.size() >= 14 && a.first.substr(a.first.size() - 14) == "/manifest.json") {
            auto m = Json::parse(a.second);
            bool build = false, board = false;
            for (auto &b : m.at("requires").at("builds"))
                if (b == "7E18" || b == "7*")
                    build = true;
            for (auto &b : m.at("requires").at("boards"))
                if (b == "n72ap")
                    board = true;
            if (build && board) {
                require(family.empty(), "ambiguous seed package");
                family = a.first.substr(0, a.first.size() - 14);
                man = m;
            }
        }
    require(!family.empty() && man.at("arch") == "armv6", "missing armv6 baseline package");
    bool legacy = man.at("requires").value("link", "") == "legacy";
    require(e.at("loader/hook-provenance") == bytes("file-or-absence 1\n"), "loader cannot restore absent stock hooks");
    auto loader = e.at(legacy ? "loader/it_boot-legacy" : "loader/it_boot");
    guest_macho(loader);
    v.putfile("/usr/local/bin/it_boot", loader, "755");
    v.putfile("/System/Library/LaunchDaemons/com.qemu.it-boot.plist", e.at("loader/com.qemu.it-boot.plist"));
    Json hooks = Json::array();
    std::set<std::string> dropped;
    for (auto &h : man.at("hooks")) {
        std::string target = h.at("target");
        require(target[0] == '/' && safe(target.substr(1)), "hook target");
        if (v.exists(target))
            hooks.push_back(h);
        else
            dropped.insert(h.at("file"));
    }
    man["hooks"] = hooks;
    Json files = Json::array();
    for (auto &f : man.at("files"))
        if (!dropped.count(f.at("name")))
            files.push_back(f);
    man["files"] = files;
    auto serial = man.at("serial").get<int64_t>();
    std::string root = "/usr/local/lighttouch", pkg = root + "/pkgs/" + std::to_string(serial);
    std::map<std::string, Json> hookmap;
    for (auto &h : hooks)
        hookmap[h.at("file")] = h;
    std::set<std::string> jobs;
    for (auto &j : man.at("jobs"))
        jobs.insert(j);
    std::string offer =
        "ltpkg 1\nbuild 7E18\nserial " + std::to_string(serial) + " " + man.at("version").get<std::string>() + "\n";
    size_t i = 0;
    for (auto &f : files) {
        std::string name = f.at("name"), mode = f.at("mode"), sha = f.at("sha256");
        require(safe(name), "package file name");
        auto b = e.at(family + "/" + name);
        require(b.size() == f.at("size").get<size_t>() && digest(b) == sha, "package payload checksum");
        if (b.size() >= 4 && number(b, 0, 4) == 0xfeedface)
            guest_macho(b);
        v.putfile(pkg + "/" + name, b, mode);
        std::string kind = hookmap.count(name) ? "hook" : jobs.count(name) ? "job" : "file";
        offer +=
            kind + " " + std::to_string(i++) + " " + name + " " + mode + " " + std::to_string(b.size()) + " " + sha;
        if (hookmap.count(name)) {
            auto h = hookmap.at(name);
            offer += " " + h.at("target").get<std::string>() + (h.at("respring").get<bool>() ? " respring" : "");
        }
        offer += '\n';
    }
    v.putfile(pkg + "/offer", bytes(offer));
    v.cmd({"symlink", root + "/current", "pkgs/" + std::to_string(serial)});
    std::string state = "seed " + std::to_string(serial) + "\n";
    for (auto &h : hooks) {
        std::string target = h.at("target"), name = h.at("file");
        if (!v.exists(target + ".baked") && !v.exists(target + ".baked-absent"))
            v.putfile(target + ".baked", v.get(target), "755");
        std::string mode = "755";
        for (auto &f : files)
            if (f.at("name") == name)
                mode = f.at("mode");
        v.putfile(target, e.at(family + "/" + name), mode);
        state += "hook " + std::to_string(h.at("respring").get<bool>() ? 1 : 0) + " " + target + "\n";
    }
    v.putfile(root + "/state", bytes(state));
    Json jobnames = Json::array();
    for (auto &j : jobs) {
        std::string basename = fs::path(j).filename();
        std::string target = "/System/Library/LaunchDaemons/" + basename;
        if (v.exists(target))
            v.cmd({"rm", target});
        jobnames.push_back(basename);
    }
    Json targets = Json::array();
    for (auto &h : hooks)
        targets.push_back(h.at("target"));
    return {{"family", family},
            {"seed", serial},
            {"version", man.at("version")},
            {"gles", true},
            {"itpack", {{"path", "armv6.itpack"}, {"sha256", digest(packed)}}},
            {"hooks", targets},
            {"jobs", jobnames}};
}
void grow_hfsx(const fs::path &image, uint32_t newBlocks) {
    std::fstream f(image, std::ios::binary | std::ios::in | std::ios::out);
    require(bool(f), "HFS growth open");
    Bytes h(512);
    f.seekg(1024);
    f.read((char *)h.data(), 512);
    require(f.good() && number(h, 0, 2, true) == 0x4858 && number(h, 40, 4, true) == 4096, "HFS growth geometry");
    uint32_t oldBlocks = number(h, 44, 4, true), oldFree = number(h, 48, 4, true);
    require(newBlocks > oldBlocks && fs::file_size(image) == uint64_t(oldBlocks) * 4096, "HFS growth extent");
    uint64_t oldSize = number(h, 112, 8, true);
    require(oldSize > 0 && oldSize <= 1024 * 1024 && oldSize >= (oldBlocks + 7) / 8, "allocation bitmap extent");
    Bytes bitmap(((uint64_t(newBlocks) + 32767) / 32768) * 4096);
    require(oldSize <= bitmap.size(), "allocation bitmap exceeds grown extent");
    size_t off = 0;
    std::vector<std::pair<uint32_t, uint32_t>> oldExtents;
    for (int i = 0; i < 8 && off < oldSize; i++) {
        uint32_t start = number(h, 128 + i * 8, 4, true), count = number(h, 132 + i * 8, 4, true);
        require(count && start < oldBlocks && count <= oldBlocks - start, "allocation bitmap extents");
        size_t n = std::min<uint64_t>(oldSize - off, uint64_t(count) * 4096);
        f.seekg(uint64_t(start) * 4096);
        f.read((char *)bitmap.data() + off, n);
        require(f.good(), "read allocation bitmap");
        off += n;
        oldExtents.emplace_back(start, count);
    }
    require(off == oldSize, "allocation overflow extents unsupported");
    for (uint32_t k = oldBlocks; k < newBlocks; k++)
        bitmap[k / 8] &= ~(0x80 >> (k % 8));
    uint32_t mapBlocks = bitmap.size() / 4096;
    require(mapBlocks < newBlocks - oldBlocks - 1, "bitmap growth capacity");
    auto bit = [&](uint32_t block, bool used) {
        require(block < newBlocks, "allocation block");
        uint8_t mask = 0x80 >> (block % 8);
        if (used)
            bitmap[block / 8] |= mask;
        else
            bitmap[block / 8] &= ~mask;
    };
    uint32_t retired = 0;
    for (auto &e : oldExtents)
        for (uint32_t k = 0; k < e.second; k++) {
            bit(e.first + k, false);
            retired++;
        }
    bit(oldBlocks - 1, false);
    for (uint32_t k = 0; k < mapBlocks; k++)
        bit(oldBlocks + k, true);
    bit(newBlocks - 1, true);
    put(h, 44, newBlocks, 4, true);
    put(h, 48, uint64_t(oldFree) + newBlocks - oldBlocks - mapBlocks + retired, 4, true);
    put(h, 52, oldBlocks + mapBlocks, 4, true);
    put(h, 112, bitmap.size(), 8, true);
    put(h, 124, mapBlocks, 4, true);
    std::fill(h.begin() + 128, h.begin() + 192, 0);
    put(h, 128, oldBlocks, 4, true);
    put(h, 132, mapBlocks, 4, true);
    fs::resize_file(image, uint64_t(newBlocks) * 4096);
    f.seekp(uint64_t(oldBlocks) * 4096);
    f.write((char *)bitmap.data(), bitmap.size());
    f.seekp(1024);
    f.write((char *)h.data(), 512);
    f.seekp(uint64_t(newBlocks) * 4096 - 1024);
    f.write((char *)h.data(), 512);
    f.flush();
    require(f.good(), "write grown bitmap/header");
}
static void fsync_path(const fs::path &p, bool dir = false) {
    int fd = open(p.c_str(), O_RDONLY | O_CLOEXEC | (dir ? O_DIRECTORY : 0));
    if (fd < 0)
        throw Error(LT_FW_IO, "open for fsync " + p.string());
    int result = fsync(fd);
    close(fd);
    if (result)
        throw Error(LT_FW_IO, "fsync " + p.string());
}
static void prepare(const lt_fw_request &r) {
    short endian = 1;
    endianness = *(char *)&endian ? IS_LITTLE_ENDIAN : IS_BIG_ENDIAN;
    auto catalog = Json::parse(read(r.catalog_path, 16 * 1024 * 1024));
    Json entry;
    for (auto &e : catalog.at("entries"))
        if (e.at("id") == r.firmware_id) {
            require(entry.is_null(), "duplicate catalog ID");
            entry = e;
        }
    if (entry.is_null() || entry.at("id") != "n72ap-7E18")
        throw Error(LT_FW_UNSUPPORTED, "Only n72ap-7E18 baseline preparation is implemented");
    require(entry.at("board") == "n72ap" && entry.at("build") == "7E18" && entry.at("version") == "3.1.3" &&
                entry.at("recipe").at("system_mib") == 7168,
            "unsupported baseline recipe");
    require(entry.at("source").at("sha1") == "5f4f5c01eda2f811f73167e7d1f82dbeed82367b" &&
                entry.at("source").at("bytes") == 263275211,
            "baseline catalog source pin mismatch");
    progress(r, "verify IPSW");
    if (hash_file(r.ipsw_path, r, true) != entry.at("source").at("sha1").get<std::string>())
        throw Error(LT_FW_CHECKSUM, "Stock IPSW SHA1 does not match n72ap-7E18");
    require(fs::file_size(r.ipsw_path) == entry.at("source").at("bytes").get<uint64_t>(), "IPSW byte count");
    auto manifest = Json::parse(read(r.guest_manifest_path, 32 * 1024 * 1024));
    require(manifest.at("schema") == 1, "unsupported guest inventory schema");
    std::map<std::string, std::string> hashes;
    for (auto &e : manifest.at("entries")) {
        if (e.value("type", "file") != "file" || !e.contains("sha256"))
            continue;
        std::string p = e.at("path");
        if (p.rfind("release-guest-unpacked/guest-tools/", 0) == 0)
            p = p.substr(std::string("release-guest-unpacked/guest-tools/").size());
        else if (p.rfind("guest-tools/", 0) == 0)
            p = p.substr(12);
        else
            continue;
        hashes[p] = e.at("sha256");
    }
    std::map<std::string, Bytes> tools;
    for (auto name :
         {"OpenGLES", "libappsync.dylib", "armv6.itpack", "it_agent", "it_typein.dylib", "sblaunch", "sbdlicon"}) {
        auto b = read(fs::path(r.guest_tools_path) / name);
        if (!hashes.count(name) || digest(b) != hashes.at(name))
            throw Error(LT_FW_CHECKSUM, "Guest export checksum missing/mismatch " + std::string(name));
        if (std::string(name) != "armv6.itpack")
            guest_macho(b);
        tools[name] = std::move(b);
    }
    fs::path dest = fs::absolute(r.destination).lexically_normal(), parent = dest.parent_path();
    require(dest.filename() != "." && dest.filename() != ".." && !dest.filename().empty(), "destination path");
    if (fs::exists(dest) || fs::is_symlink(dest))
        throw Error(LT_FW_IO, "destination exists");
    require(fs::is_directory(parent), "destination parent must exist");
    std::string tmp = (parent / ("." + dest.filename().string() + ".prepare-XXXXXX")).string();
    require(mkdtemp(tmp.data()), "cannot create transaction directory");
    fs::path stage = tmp;
    bool promoted = false;
    try {
        fs::path work = stage / "work";
        fs::create_directory(work);
        write(stage / "PREPARING", bytes("LightTouch preparation transaction ABI1\n"));
        Zip zip(r.ipsw_path);
        auto restore = plist(zip.get("Restore.plist", 8 * 1024 * 1024));
        require(restore.at("ProductVersion") == "3.1.3" && restore.at("ProductBuildVersion") == "7E18" &&
                    restore.at("ProductType") == "iPod2,1",
                "IPSW restore identity");
        int epoch = 0;
        for (auto &d : restore.at("DeviceMap"))
            if (d.at("BoardConfig") == "n72ap") {
                require(!epoch, "duplicate restore board");
                epoch = d.at("SCEP");
            }
        require(epoch >= 1 && epoch <= 9, "restore NAND epoch");
        auto keys = entry.at("keys");
        auto dec = [&](const char *name) {
            auto k = keys.at(name);
            return decrypt_img3(zip.get(zip.basename(k.at("file"))), k.at("key"), k.at("iv"));
        };
        auto iboot = dec("iBoot");
        std::string text(iboot.begin(), iboot.end()), prefix = "/System/Library/Caches/com.apple.kernelcaches/", kc;
        size_t pos = 0;
        while ((pos = text.find(prefix, pos)) != std::string::npos) {
            size_t end = pos;
            while (end < text.size() && (unsigned char)text[end] >= 33 && (unsigned char)text[end] <= 126)
                end++;
            std::string p = text.substr(pos, end - pos);
            require(kc.empty() || kc == p, "ambiguous iBoot kernel path");
            kc = p;
            pos = end;
        }
        require(!kc.empty() && safe(kc.substr(1)), "missing kernelcache path");
        auto id = identity();
        std::string flash = "Firmware/all_flash/all_flash.n72ap.production/";
        // Obtain manifest once: iterators must share the same buffer.
        auto flashlist = zip.get(flash + "manifest");
        std::istringstream names(std::string(flashlist.begin(), flashlist.end()));
        std::map<std::string, Bytes> images;
        std::set<std::string> types;
        std::string name;
        while (names >> name) {
            require(safe(name) && name.find('/') == std::string::npos, "NOR manifest path");
            auto b = zip.get(flash + name);
            tags(b);
            std::string type(b.begin() + 16, b.begin() + 20);
            std::reverse(type.begin(), type.end());
            require(images.emplace(type, b).second, "duplicate NOR image type");
            types.insert(type);
        }
        std::vector<std::string> order;
        for (auto t : {"illb", "ibot", "dtre", "logo", "nsrv", "bat0", "bat1", "recm", "glyC", "glyP"})
            if (types.count(t))
                order.push_back(t);
        require(types.count("ibot") && types.count("illb") && types.count("dtre"), "missing boot NOR image");
        write(stage / "nor.bin", nor(id, images, order));
        write(stage / "iBoot.bin", iboot);
        Bytes blobs;
        for (auto it = keys.begin(); it != keys.end(); ++it) {
            auto k = it.value();
            if (!k.contains("iv"))
                continue;
            auto material = hex(k.at("iv").get<std::string>());
            auto key = hex(k.at("key").get<std::string>());
            material.insert(material.end(), key.begin(), key.end());
            if (material.size() != 32)
                continue;
            auto b = zip.get(zip.basename(k.at("file")));
            auto ts = tags(b);
            if (!ts.count("KBAG"))
                continue;
            auto tag = ts.at("KBAG");
            if (tag.length < 40 || number(b, tag.offset + 12, 4) != 1 || number(b, tag.offset + 16, 4) != 128)
                continue;
            blobs.insert(blobs.end(), b.begin() + tag.offset + 20, b.begin() + tag.offset + 52);
            blobs.insert(blobs.end(), material.begin(), material.end());
        }
        write(stage / "gid-blobs.bin", blobs);
        write(stage / "identity.json", bytes(id.dump(2)));
        progress(r, "decrypt rootfs");
        auto root = keys.at("rootfs");
        auto encrypted = work / "rootfs.dmg", image = work / "volume.img";
        write(encrypted, zip.get(zip.basename(root.at("file")), 1024 * 1024 * 1024));
        require(lt_fw_guard_dmg(encrypted.c_str(), image.c_str(), root.at("key").get<std::string>().c_str()),
                "DMG extraction failed; restart firmware service after fatal parser error");
        Volume v{image, work, r};
        constexpr int blocks = 1835008;
        grow_hfsx(image, blocks);
        std::ifstream vf(image, std::ios::binary);
        Bytes header(512);
        vf.seekg(1024);
        vf.read((char *)header.data(), 512);
        require(vf.good() && number(header, 0, 2, true) == 0x4858 && number(header, 40, 4, true) == 4096 &&
                    number(header, 44, 4, true) == blocks && fs::file_size(image) == uint64_t(blocks) * 4096,
                "HFSX resized geometry");
        vf.close();
        progress(r, "install baseline system edits");
        v.putfile("/private/etc/fstab", bytes("/dev/disk0s1 / hfs rw 0 1\n"));
        v.putfile(kc, zip.get(zip.basename(keys.at("kernelcache").at("file"))));
        std::string gl = "/System/Library/Frameworks/OpenGLES.framework/OpenGLES";
        if (v.exists(gl))
            v.putfile(gl + ".baked", v.get(gl), "755");
        else
            v.putfile(gl + ".baked-absent", {});
        v.putfile(gl, tools.at("OpenGLES"), "755");
        v.putfile("/System/Library/Caches/com.apple.dyld/enable-dylibs-to-override-cache", {});
        v.edit("/System/Library/LaunchDaemons/com.apple.SpringBoard.plist", [](Json &j) {
            auto &env = j["EnvironmentVariables"];
            if (env.is_null())
                env = Json::object();
            for (auto k : {"CA_ENABLE_OGL", "LK_ENABLE_OGL"})
                env[k] = "1";
            for (auto k : {"CA_AUTO_ENABLE_OGL", "LK_AUTO_ENABLE_OGL", "CA_ENABLE_MBX2D", "LK_ENABLE_MBX2D"})
                env[k] = "0";
        });
        v.putfile("/usr/local/bin/it_agent", tools.at("it_agent"), "755");
        v.putfile("/usr/local/bin/sblaunch", tools.at("sblaunch"), "755");
        v.putfile("/usr/local/bin/sbdlicon", tools.at("sbdlicon"), "755");
        v.putfile("/usr/lib/it_typein.dylib", tools.at("it_typein.dylib"), "755");
        v.edit("/System/Library/LaunchDaemons/com.apple.SpringBoard.plist", [](Json &j) {
            auto &env = j["EnvironmentVariables"];
            std::string old = env.value("DYLD_INSERT_LIBRARIES", "");
            std::istringstream list(old);
            std::string part, out;
            while (std::getline(list, part, ':'))
                if (!part.empty() && part != "/usr/lib/it_kbd_agent.dylib" && part != "/usr/lib/it_typein.dylib")
                    out += (out.empty() ? "" : ":") + part;
            env["DYLD_INSERT_LIBRARIES"] = out + (out.empty() ? "" : ":") + std::string("/usr/lib/it_typein.dylib");
        });
        v.putfile("/usr/lib/libappsync.dylib", tools.at("libappsync.dylib"), "755");
        std::string install;
        for (auto name : {"com.apple.mobile.installd.plist", "com.apple.installd.plist"}) {
            std::string p = "/System/Library/LaunchDaemons/" + std::string(name);
            if (v.exists(p)) {
                install = p;
                break;
            }
        }
        require(!install.empty(), "missing installation job");
        v.edit(install, [](Json &j) {
            auto &env = j["EnvironmentVariables"];
            if (env.is_null())
                env = Json::object();
            std::string old = env.value("DYLD_INSERT_LIBRARIES", "");
            if (old.find("/usr/lib/libappsync.dylib") == std::string::npos)
                old += (old.empty() ? "" : ":") + std::string("/usr/lib/libappsync.dylib");
            env["DYLD_INSERT_LIBRARIES"] = old;
        });
        auto lockdown = v.get("/usr/libexec/lockdownd");
        std::string activationBefore = digest(lockdown);
        const char *error = nullptr;
        LTActivationReport report{};
        require(lt_activate_report(lockdown.data(), lockdown.size(), &report, &error),
                error ? error : "activation failed");
        auto activated = resign(lockdown);
        v.putfile("/usr/libexec/lockdownd", activated, "755");
        require(report.strategy && report.isa && report.width <= 16, "activation report bounds");
        auto reporthex = [&](const uint8_t *data) {
            std::string out;
            for (unsigned i = 0; i < report.width; i++) {
                out += "0123456789abcdef"[data[i] >> 4];
                out += "0123456789abcdef"[data[i] & 15];
            }
            return out;
        };
        Json activation = {{"input_sha256", activationBefore},
                           {"output_sha256", digest(activated)},
                           {"patch",
                            {{"strategy", report.strategy},
                             {"isa", report.isa},
                             {"offset", report.offset},
                             {"original", reporthex(report.original)},
                             {"replacement", reporthex(report.replacement)}}}};
        v.putfile("/private/var/mobile/Library/Preferences/com.apple.springboard.plist",
                  xml({{"SBDidShowReorderText", true}}), "644", 501);
        auto seedplist = [&](const std::string &p, Json changes, int uid) {
            Json j = v.exists(p) ? plist(v.get(p)) : Json::object();
            j.update(changes);
            v.putfile(p, xml(j), "644", uid);
        };
        seedplist("/private/var/mobile/Library/Preferences/com.apple.mobilemail.plist",
                  {{"PlayNewMailSound", true}, {"PlaySentMailSound", true}}, 501);
        seedplist("/private/var/mobile/Library/Preferences/com.apple.springboard.plist",
                  {{"calendar-alarm", "/Applications/MobileCal.app/alarm.aiff"}, {"lock-unlock", true}}, 501);
        seedplist("/private/var/mobile/Library/Preferences/com.apple.preferences.sounds.plist", {{"keyboard", true}},
                  501);
        for (int i = 1; i <= 3; i++)
            v.putfile("/private/var/mobile/Media/.lt-guest-tools-v" + std::to_string(i),
                      bytes("v" + std::to_string(i) + "\n"), "644", 501);
        const std::string pac = R"PAC(function FindProxyForURL(url, host) {
 if (isPlainHostName(host)) return "DIRECT";
 if (/^\d+\.\d+\.\d+\.\d+$/.test(host) &&
 (isInNet(host, "10.0.0.0", "255.0.0.0") || isInNet(host, "172.16.0.0", "255.240.0.0") ||
 isInNet(host, "192.168.0.0", "255.255.0.0") || isInNet(host, "169.254.0.0", "255.255.0.0") ||
 isInNet(host, "127.0.0.0", "255.0.0.0"))) return "DIRECT";
 return "PROXY 10.0.2.100:3128; DIRECT";
}
)PAC";
        v.putfile("/usr/share/ltm/proxy.pac", bytes(pac));
        std::string netpath = "/private/var/preferences/SystemConfiguration/preferences.plist";
        Json prefs = v.exists(netpath) ? plist(v.get(netpath)) : Json::object();
        std::string set = prefs.value("CurrentSet", "/Sets/4C54E7A1-0B5E-4D6B-9A1C-534554000001");
        prefs["CurrentSet"] = set;
        set = fs::path(set).filename();
        std::string svc = "4C54E7A1-0B5E-4D6B-9A1C-574946490000";
        auto &service = prefs["NetworkServices"][svc];
        if (service.is_null())
            service = {{"Interface",
                        {{"DeviceName", "en0"},
                         {"Hardware", "AirPort"},
                         {"Type", "Ethernet"},
                         {"UserDefinedName", "AirPort"}}},
                       {"IPv4", {{"ConfigMethod", "DHCP"}}},
                       {"IPv6", {{"ConfigMethod", "Automatic"}}},
                       {"DNS", Json::object()},
                       {"UserDefinedName", "AirPort"}};
        service["Proxies"] = {{"ExceptionsList", Json::array({"*.local", "169.254/16"})},
                              {"FTPPassive", 1},
                              {"ProxyAutoConfigEnable", 1},
                              {"ProxyAutoConfigURLString", "file:///usr/share/ltm/proxy.pac"}};
        auto &network = prefs["Sets"][set]["Network"];
        network["Service"][svc] = {{"__LINK__", "/NetworkServices/" + svc}};
        if (network["Interface"]["en0"].is_null())
            network["Interface"]["en0"] = {{"AirPort", {{"JoinMode", "Automatic"}}}};
        auto &oldorder = network["Global"]["IPv4"]["ServiceOrder"];
        Json neworder = Json::array({svc});
        if (oldorder.is_array())
            for (auto &x : oldorder)
                if (x != svc)
                    neworder.push_back(x);
        oldorder = neworder;
        v.putfile(netpath, xml(prefs));
        auto package = seed(v, tools.at("armv6.itpack"));
        progress(r, "write NAND");
        fs::create_directory(stage / "nand");
        for (int cs = 0; cs < 4; cs++)
            fs::create_directory(stage / "nand" / ("cs" + std::to_string(cs)));
        auto pagewrite = [&](std::pair<int, int> p, const Bytes &b) {
            write(stage / "nand" / ("cs" + std::to_string(p.first)) / (std::to_string(p.second) + ".page"), b);
        };
        for (auto &m : metadata(blocks, epoch))
            pagewrite(m.first, m.second);
        std::ifstream volume(image, std::ios::binary);
        Bytes b(4160);
        b[4104] = 255;
        b[4106] = 255;
        for (int i = 0; i < blocks; i++) {
            if (i % 1024 == 0)
                progress(r, "write NAND", i, blocks);
            require(bool(volume.read((char *)b.data(), 4096)), "volume short read");
            if (std::any_of(b.begin(), b.begin() + 4096, [](uint8_t x) { return x != 0; }))
                pagewrite(page(i), b);
        }
        volume.close();
        fs::remove_all(work);
        Json lock = {
            {"format", 1},
            {"board", "n72ap"},
            {"build", "7E18"},
            {"product_version", "3.1.3"},
            {"product_type", "iPod2,1"},
            {"boot_strategy", "iboot"},
            {"identity",
             {{"seed", id.at("seed")}, {"udid", id.at("udid")}, {"sha256", digest(read(stage / "identity.json"))}}},
            {"machine",
             {{"aes-uid", "engine"},
              {"wifi-mac", id.at("wifi-mac")},
              {"bt-mac", id.at("bt-mac")},
              {"ecid", id.at("unique-chip-id")}}},
            {"tool", {{"name", "LightTouchPortable"}, {"version", "1"}}},
            {"source", {{"sha1", entry.at("source").at("sha1")}}},
            {"derived",
             {{"storage_layout", "n72-generated-v1"}, {"nand_epoch", epoch}, {"kernelcache_path", kc.substr(1)}}},
            {"guest_package", package}};
        Json checks = Json::object();
        for (auto &file : fs::recursive_directory_iterator(stage))
            if (file.is_regular_file() && file.path().filename() != "PREPARING")
                checks[fs::relative(file.path(), stage).generic_string()] = hash_file(file.path(), r);
        std::string listing;
        size_t pages = 0;
        for (auto it = checks.begin(); it != checks.end(); ++it)
            if (it.key().rfind("nand/", 0) == 0) {
                listing += it.key().substr(5) + " " + it.value().get<std::string>() + "\n";
                pages++;
            }
        lock["outputs"]["nand"] = {{"path", "nand"},
                                   {"listing_sha256", digest(bytes(listing))},
                                   {"built_listing_sha256", digest(bytes(listing))},
                                   {"pages", pages}};
        for (auto item : std::vector<std::pair<std::string, std::string>>{
                 {"nor", "nor.bin"}, {"iboot", "iBoot.bin"}, {"gid_blobs", "gid-blobs.bin"}})
            lock["outputs"][item.first] = {{"path", item.second}, {"sha256", checks.at(item.second)}};
        lock["entry"] = {{"id", "n72ap-7E18"}, {"content", entry}, {"sha256", digest(bytes(entry.dump()))}};
        lock["storage"] = "8g";
        std::time_t now = std::time(nullptr);
        std::tm utc{};
        gmtime_r(&now, &utc);
        char date[32];
        std::strftime(date, sizeof(date), "%Y-%m-%dT%H:%M:%SZ", &utc);
        lock["created"] = date;
        lock["derived"]["nor_images"] = order;
        lock["derived"]["wrap_shsh_types"] = order;
        for (auto &tool : tools)
            lock["tool"]["built"]["guest tools"][tool.first] = digest(tool.second);
        lock["inputs"] = {{"ipsw", {{"path", r.ipsw_path}, {"sha1", entry.at("source").at("sha1")}}},
                          {"identity", "identity.json"},
                          {"guest_tools", r.guest_tools_path},
                          {"kernelcache", zip.basename(keys.at("kernelcache").at("file"))},
                          {"iboot", "iBoot.bin"},
                          {"all_flash", flash},
                          {"activation", activation}};
        write(stage / "device.lock.json", bytes(lock.dump(2)));
        checks["device.lock.json"] = digest(read(stage / "device.lock.json"));
        write(stage / "preparation.sha256.json",
              bytes(Json{{"size", sizeof(uint32_t) * 2}, {"version", 1}, {"files", checks}}.dump(2)));
        fs::remove(stage / "PREPARING");
        for (auto &file : fs::recursive_directory_iterator(stage / "nand"))
            fs::permissions(file.path(), fs::perms::owner_write | fs::perms::group_write | fs::perms::others_write,
                            fs::perm_options::remove);
        for (auto name : {"nand", "nor.bin", "iBoot.bin", "gid-blobs.bin"})
            fs::permissions(stage / name, fs::perms::owner_write | fs::perms::group_write | fs::perms::others_write,
                            fs::perm_options::remove);
        progress(r, "flush transaction");
        for (auto &file : fs::recursive_directory_iterator(stage))
            if (file.is_regular_file())
                fsync_path(file.path());
        std::vector<fs::path> dirs;
        for (auto &file : fs::recursive_directory_iterator(stage))
            if (file.is_directory())
                dirs.push_back(file.path());
        std::sort(dirs.begin(), dirs.end(), [](auto &a, auto &b) { return a.string().size() > b.string().size(); });
        for (auto &d : dirs)
            fsync_path(d, true);
        fsync_path(stage, true);
        progress(r, "promote transaction");
#if defined(SYS_renameat2)
        if (syscall(SYS_renameat2, AT_FDCWD, stage.c_str(), AT_FDCWD, dest.c_str(), 1) != 0)
            throw Error(LT_FW_IO, "atomic promotion failed: " + std::string(strerror(errno)));
#else
        throw Error(LT_FW_UNSUPPORTED, "rename-no-replace syscall required");
#endif
        promoted = true;
        fsync_path(parent, true);
    } catch (...) {
        if (!promoted) {
            std::error_code ec;
            for (auto &file :
                 fs::recursive_directory_iterator(stage, fs::directory_options::skip_permission_denied, ec))
                if (file.is_directory())
                    fs::permissions(file.path(), fs::perms::owner_write, fs::perm_options::add, ec);
            fs::remove_all(stage, ec);
        }
        throw;
    }
}
} // namespace ltf
extern "C" uint32_t lt_firmware_api_version() { return LT_FIRMWARE_ABI; }
extern "C" lt_fw_status lt_firmware_prepare(const lt_fw_request *r, lt_fw_error *error) {
    auto fail = [&](lt_fw_status s, const char *m) {
        if (error && error->size >= sizeof(*error) && error->version == LT_FIRMWARE_ABI) {
            error->status = s;
            snprintf(error->message, sizeof(error->message), "%s", m);
        }
        return s;
    };
    if (!r || r->size < sizeof(*r) || r->version != LT_FIRMWARE_ABI || !r->catalog_path || !r->firmware_id ||
        !r->ipsw_path || !r->guest_tools_path || !r->destination || !r->guest_manifest_path)
        return fail(LT_FW_ARGUMENT, "invalid versioned firmware request");
    if (ltf::busy.test_and_set())
        return fail(LT_FW_BUSY, "another preparation owns XPwn state");
    struct Unlock {
        ~Unlock() { ltf::busy.clear(); }
    } unlock;
    try {
        ltf::prepare(*r);
        return fail(LT_FW_OK, "prepared");
    } catch (const ltf::Error &e) {
        if (lt_fw_xpwn_poisoned())
            return fail(LT_FW_RESTART_REQUIRED, "XPwn terminal parser failure: dispose and restart firmware service");
        return fail(e.status, e.what());
    } catch (const std::filesystem::filesystem_error &e) {
        return fail(LT_FW_IO, e.what());
    } catch (const std::exception &e) {
        return fail(LT_FW_FORMAT, e.what());
    }
}

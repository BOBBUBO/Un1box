#include "Internal.hpp"
#include <fcntl.h>
#include <sys/file.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <unistd.h>
#include <algorithm>
#include <memory>
#include <array>
namespace lt {
static std::string quote(const std::string &s) {
    std::string out = "\"";
    static const char h[] = "0123456789abcdef";
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') {
            out += '\\';
            out += char(c);
        } else if (c < 32) {
            out += "\\u00";
            out += h[c >> 4];
            out += h[c & 15];
        } else
            out += char(c);
    }
    return out + '"';
}
static void record_id(const std::string &id) {
    require(!id.empty() && id.size() < 64 &&
                id.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-") ==
                    std::string::npos,
            "invalid record ID");
}
static void text(char *out, size_t capacity, const std::string &s) {
    require(s.size() < capacity, "record field exceeds ABI bound");
    memcpy(out, s.c_str(), s.size() + 1);
}
static void copy_regular(const fs::path &source, const fs::path &destination, bool immutable = false) {
    require(fs::is_regular_file(fs::symlink_status(source)), "record asset must be regular file");
    fs::copy_file(source, destination, fs::copy_options::none);
    int fd = open(destination.c_str(), O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    io(fd >= 0, "open copied record file");
    int result = fsync(fd);
    close(fd);
    io(result == 0, "sync copied record file");
    io(chmod(destination.c_str(), immutable ? 0400 : 0600) == 0, "private copied file mode");
}
static void copy_state(const fs::path &source, const fs::path &destination) {
    fs::create_directories(destination);
    for (const auto &e : fs::recursive_directory_iterator(source)) {
        auto relative = e.path().lexically_relative(source);
        if (relative == ".lease")
            continue;
        require(!e.is_symlink(), "legacy state symlink refused");
        auto target = destination / relative;
        if (e.is_directory())
            fs::create_directories(target);
        else {
            require(e.is_regular_file(), "legacy state special file refused");
            fs::create_directories(target.parent_path());
            copy_regular(e.path(), target);
        }
    }
    for (const auto &e : fs::recursive_directory_iterator(destination))
        if (e.is_directory())
            sync_dir(e.path());
    sync_dir(destination);
}
static void discard(const fs::path &p) {
    std::error_code error;
    for (const auto &e : fs::recursive_directory_iterator(p, error))
        if (e.is_directory())
            chmod(e.path().c_str(), 0700);
    chmod(p.c_str(), 0700);
    fs::remove_all(p, error);
}
static std::string new_id() {
    std::array<uint8_t, 16> b{};
    size_t offset = 0;
    while (offset < b.size()) {
        ssize_t n = getrandom(b.data() + offset, b.size() - offset, 0);
        if (n < 0 && errno == EINTR)
            continue;
        io(n > 0, "record identity entropy");
        offset += size_t(n);
    }
    static const char hex[] = "0123456789abcdef";
    std::string id;
    for (auto c : b) {
        id += hex[c >> 4];
        id += hex[c & 15];
    }
    return id;
}
} // namespace lt
struct lt_library {
    lt::fs::path root;
};
namespace lt {
struct LibraryLock {
    int fd = -1;
    explicit LibraryLock(lt_library *l) {
        require(l, "null library");
        fd = open((l->root / ".library-lock").c_str(), O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
        io(fd >= 0, "library mutation lock");
        if (flock(fd, LOCK_EX | LOCK_NB) != 0) {
            close(fd);
            fd = -1;
            throw Failure(LT_BUSY, "device library operation in progress");
        }
    }
    ~LibraryLock() {
        if (fd >= 0)
            close(fd);
    }
};
static std::vector<std::string> records(lt_library *l) {
    std::vector<std::string> ids;
    for (const auto &e : fs::directory_iterator(l->root / "records")) {
        auto id = e.path().filename().string();
        if (id.empty() || id[0] == '.')
            continue;
        record_id(id);
        require(e.is_directory() && !e.is_symlink(), "invalid record storage");
        require(fs::is_regular_file(fs::symlink_status(e.path() / "record.json")), "incomplete device record");
        ids.push_back(id);
    }
    std::sort(ids.begin(), ids.end());
    return ids;
}
static std::string selected(lt_library *l) {
    if (!fs::exists(l->root / "selected"))
        return "";
    std::string id = read_file(l->root / "selected", 64);
    record_id(id);
    return id;
}
static void describe(lt_library *l, const std::string &id, lt_record_info *out) {
    abi(out);
    record_id(id);
    auto directory = l->root / "records" / id;
    auto j = parse(read_file(directory / "record.json", 65536));
    require(j.at("schema").integer() == 1 && j.at("id").string() == id, "invalid device record schema/identity");
    text(out->id, sizeof out->id, id);
    text(out->display_name, sizeof out->display_name, j.at("name").string());
    text(out->board, sizeof out->board, j.at("board").string());
    text(out->build, sizeof out->build, j.at("build").string());
    text(out->base_sha256, sizeof out->base_sha256, j.at("base_sha256").string());
    text(out->base_path, sizeof out->base_path, (directory / "base").string());
    text(out->state_path, sizeof out->state_path, (directory / "state").string());
    text(out->bootrom_path, sizeof out->bootrom_path,
         fs::exists(directory / "assets/bootrom.bin") ? (directory / "assets/bootrom.bin").string() : "");
    text(out->guest_package_path, sizeof out->guest_package_path,
         fs::exists(directory / "assets/guest.itpack") ? (directory / "assets/guest.itpack").string() : "");
    out->selected = selected(l) == id;
    out->dirty = fs::exists(directory / "state/.dirty");
}
static void import(lt_library *l, const lt_library_import_config *c, const char *legacy_state, lt_record_info *out) {
    abi(c);
    abi(out);
    require(c->source_path, "record source missing");
    std::unique_ptr<lt_device, decltype(&lt_device_close)> legacy(nullptr, lt_device_close);
    if (legacy_state) {
        require(records(l).empty(), "legacy adoption only into empty library", LT_BUSY);
        lt_device_config old{sizeof(old), 1, c->source_path, legacy_state, 1};
        lt_device *d = nullptr;
        lt_error e{sizeof(e), 1, 0, {}};
        auto status = lt_device_open(&old, &d, &e);
        require(status == LT_OK, e.message, status);
        legacy.reset(d);
    }
    std::string id = new_id();
    std::string pattern = (l->root / "records/.record-stage-XXXXXX").string();
    std::vector<char> tmp(pattern.begin(), pattern.end());
    tmp.push_back(0);
    io(mkdtemp(tmp.data()) != nullptr, "record staging");
    fs::path stage = tmp.data();
    try {
        std::string base = (stage / "base").string();
        lt_import_config cfg{sizeof(cfg),  1,           c->source_path, base.c_str(), c->expected_sha256,
                             c->max_bytes, c->progress, c->opaque};
        lt_error e{sizeof(e), 1, 0, {}};
        lt_status status = fs::is_directory(fs::symlink_status(c->source_path)) ? lt_import_directory(&cfg, &e)
                                                                                : lt_import_archive(&cfg, &e);
        require(status == LT_OK, e.message, status);
        auto lock = parse(read_file(stage / "base/device.lock.json"));
        std::string board = lock.at("board").string(), build = lock.at("build").string(),
                    name = c->display_name && *c->display_name ? c->display_name : board + " " + build;
        require(name.size() < 128, "device name too long");
        fs::create_directory(stage / "assets");
        if (c->bootrom_path && *c->bootrom_path)
            copy_regular(c->bootrom_path, stage / "assets/bootrom.bin", true);
        if (c->guest_package_path && *c->guest_package_path)
            copy_regular(c->guest_package_path, stage / "assets/guest.itpack", true);
        else if (const auto *outputs = lock.find("outputs"))
            if (const auto *package = outputs->find("guest_package")) {
                auto source = stage / "base" / package->at("path").string();
                require(digest_file(source) == package->at("sha256").string(),
                        "prepared guest package checksum mismatch");
                copy_regular(source, stage / "assets/guest.itpack", true);
            }
        if (legacy_state)
            copy_state(legacy->state, stage / "state");
        else
            fs::create_directory(stage / "state");
        std::string metadata = "{\"schema\":1,\"id\":" + quote(id) + ",\"name\":" + quote(name) +
                               ",\"board\":" + quote(board) + ",\"build\":" + quote(build) +
                               ",\"base_sha256\":" + quote(digest_file(stage / "base/device.lock.json"));
        if (fs::exists(stage / "assets/bootrom.bin"))
            metadata += ",\"bootrom_sha256\":" + quote(digest_file(stage / "assets/bootrom.bin"));
        if (fs::exists(stage / "assets/guest.itpack"))
            metadata += ",\"guest_package_sha256\":" + quote(digest_file(stage / "assets/guest.itpack"));
        metadata += "}\n";
        atomic_write(stage / "record.json", metadata);
        sync_dir(stage / "assets");
        io(chmod((stage / "assets").c_str(), 0500) == 0, "immutable record assets");
        sync_dir(stage / "state");
        sync_dir(stage);
        fs::rename(stage, l->root / "records" / id);
        sync_dir(l->root / "records");
        if (selected(l).empty())
            atomic_write(l->root / "selected", id);
        describe(l, id, out);
    } catch (...) {
        discard(stage);
        throw;
    }
}
} // namespace lt
extern "C" {
lt_status lt_library_open(const lt_library_config *c, lt_library **out, lt_error *e) {
    return lt::guard(e, [&] {
        lt::abi(c);
        lt::require(out && c->root_path && lt::fs::path(c->root_path).is_absolute(),
                    "private library absolute root required");
        *out = nullptr;
        lt::fs::create_directories(c->root_path);
        lt::require(!lt::fs::is_symlink(lt::fs::symlink_status(c->root_path)), "library root symlink refused");
        auto l = std::make_unique<lt_library>();
        l->root = lt::fs::canonical(c->root_path);
        lt::require(l->root.string().size() < 900, "library path exceeds record ABI bound");
        lt::io(chmod(l->root.c_str(), 0700) == 0, "private library permissions");
        lt::fs::create_directories(l->root / "records");
        lt::require(!lt::fs::is_symlink(lt::fs::symlink_status(l->root / "records")),
                    "library records symlink refused");
        *out = l.release();
    });
}
void lt_library_close(lt_library *l) { delete l; }
lt_status lt_library_count(lt_library *l, uint32_t *out, lt_error *e) {
    return lt::guard(e, [&] {
        lt::LibraryLock lock(l);
        lt::require(out, "record count output missing");
        *out = uint32_t(lt::records(l).size());
    });
}
lt_status lt_library_record_at(lt_library *l, uint32_t index, lt_record_info *out, lt_error *e) {
    return lt::guard(e, [&] {
        lt::LibraryLock lock(l);
        auto ids = lt::records(l);
        lt::require(index < ids.size(), "record index outside library", LT_AGAIN);
        lt::describe(l, ids[index], out);
    });
}
lt_status lt_library_select(lt_library *l, const char *id, lt_error *e) {
    return lt::guard(e, [&] {
        lt::LibraryLock lock(l);
        lt::require(id, "record ID missing");
        lt_record_info info{sizeof(info), 1, {}, {}, {}, {}, {}, {}, {}, {}, {}, 0, 0};
        lt::describe(l, id, &info);
        lt::atomic_write(l->root / "selected", id);
    });
}
lt_status lt_library_selected(lt_library *l, lt_record_info *out, lt_error *e) {
    return lt::guard(e, [&] {
        lt::LibraryLock lock(l);
        auto id = lt::selected(l);
        lt::require(!id.empty(), "no device selected", LT_AGAIN);
        lt::describe(l, id, out);
    });
}
lt_status lt_library_import(lt_library *l, const lt_library_import_config *c, lt_record_info *out, lt_error *e) {
    return lt::guard(e, [&] {
        lt::LibraryLock lock(l);
        lt::import(l, c, nullptr, out);
    });
}
lt_status lt_library_adopt_legacy(lt_library *l, const lt_library_import_config *c, const char *state,
                                  lt_record_info *out, lt_error *e) {
    return lt::guard(e, [&] {
        lt::LibraryLock lock(l);
        lt::require(state, "legacy state missing");
        lt::import(l, c, state, out);
    });
}
lt_status lt_library_device_open(lt_library *l, const char *id, uint32_t recover, lt_device **out, lt_error *e) {
    return lt::guard(e, [&] {
        lt::LibraryLock lock(l);
        lt::require(id && out, "device record ID/output missing");
        *out = nullptr;
        lt_record_info info{sizeof(info), 1, {}, {}, {}, {}, {}, {}, {}, {}, {}, 0, 0};
        lt::describe(l, id, &info);
        auto metadata = lt::parse(lt::read_file(l->root / "records" / id / "record.json", 65536));
        if (*info.bootrom_path)
            lt::require(lt::digest_file(info.bootrom_path) == metadata.at("bootrom_sha256").string(),
                        "record ROM checksum mismatch");
        if (*info.guest_package_path)
            lt::require(lt::digest_file(info.guest_package_path) == metadata.at("guest_package_sha256").string(),
                        "record guest package checksum mismatch");
        lt_device_config cfg{sizeof(cfg), 1, info.base_path, info.state_path, recover};
        lt_error nested{sizeof(nested), 1, 0, {}};
        auto status = lt_device_open(&cfg, out, &nested);
        lt::require(status == LT_OK, nested.message, status);
        if ((*out)->base_digest != info.base_sha256) {
            lt_device_close(*out);
            *out = nullptr;
            throw lt::Failure(LT_INVALID, "record base integrity pin mismatch");
        }
    });
}
}

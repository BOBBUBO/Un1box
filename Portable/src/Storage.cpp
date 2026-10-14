#include "Internal.hpp"
#include <algorithm>
#include <array>
#include <fcntl.h>
#include <fstream>
#include <linux/fs.h>
#include <memory>
#include <set>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <zlib.h>
namespace lt {
struct Fd {
    int n = -1;
    explicit Fd(int f) : n(f) {}
    ~Fd() {
        if (n >= 0)
            close(n);
    }
    Fd(const Fd &) = delete;
};
static Fd open_read(const fs::path &p) {
    int fd = open(p.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    io(fd >= 0, "open " + p.string());
    struct stat st {};
    io(fstat(fd, &st) == 0, "fstat");
    require(S_ISREG(st.st_mode), "not a regular file");
    return Fd(fd);
}
static void write_all(int fd, const void *p, size_t n) {
    auto *b = static_cast<const char *>(p);
    while (n) {
        ssize_t k = write(fd, b, n);
        if (k < 0 && errno == EINTR)
            continue;
        io(k > 0, "write");
        b += k;
        n -= size_t(k);
    }
}
static void read_all(int fd, void *p, size_t n) {
    auto *b = static_cast<char *>(p);
    while (n) {
        ssize_t k = read(fd, b, n);
        if (k < 0 && errno == EINTR)
            continue;
        io(k > 0, "truncated input");
        b += k;
        n -= size_t(k);
    }
}
std::string read_file(const fs::path &p, size_t limit) {
    auto fd = open_read(p);
    struct stat st {};
    io(fstat(fd.n, &st) == 0, "fstat");
    require(st.st_size >= 0 && uint64_t(st.st_size) <= limit, "file exceeds bound");
    std::string s(size_t(st.st_size), '\0');
    read_all(fd.n, s.data(), s.size());
    return s;
}
struct AdmissionProgress {
    lt_progress_fn callback;
    void *opaque;
    uint64_t done = 0, total = 0;
    void checkpoint() const {
        require(!callback || callback(opaque, done, total) == 0, "device admission cancelled", LT_CANCELLED);
    }
    void advance(uint64_t bytes) {
        require(done <= total && bytes <= total - done, "admission byte count exceeds checked plan");
        done += bytes;
        checkpoint();
    }
};
static std::string digest_fd(int fd, AdmissionProgress *progress = nullptr, uint64_t planned_bytes = 0) {
    Sha256 hash;
    std::array<char, 65536> b{};
    uint64_t consumed = 0;
    if (progress) {
        struct stat st {};
        io(fstat(fd, &st) == 0, "fstat admission asset");
        require(st.st_size >= 0 && uint64_t(st.st_size) == planned_bytes, "admission asset size changed");
        progress->checkpoint();
    }
    for (;;) {
        ssize_t n = read(fd, b.data(), b.size());
        if (n < 0 && errno == EINTR)
            continue;
        io(n >= 0, "hash read");
        if (!n)
            break;
        if (progress)
            require(consumed <= planned_bytes && uint64_t(n) <= planned_bytes - consumed,
                    "admission asset grew while hashing");
        hash.update(b.data(), size_t(n));
        if (progress) {
            consumed += uint64_t(n);
            progress->advance(uint64_t(n));
        }
    }
    if (progress)
        require(consumed == planned_bytes, "admission asset truncated while hashing");
    return hash.finish();
}
std::string digest_file(const fs::path &p) {
    auto fd = open_read(p);
    return digest_fd(fd.n);
}
void sync_dir(const fs::path &p) {
    Fd fd(open(p.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
    io(fd.n >= 0, "open directory");
    io(fsync(fd.n) == 0, "sync directory");
}
void atomic_write(const fs::path &p, const std::string &s) {
    std::string tmp = (p.parent_path() / ".lt-write-XXXXXX").string();
    std::vector<char> b(tmp.begin(), tmp.end());
    b.push_back(0);
    Fd fd(mkstemp(b.data()));
    io(fd.n >= 0, "create metadata");
    try {
        write_all(fd.n, s.data(), s.size());
        io(fsync(fd.n) == 0, "sync metadata");
        io(rename(b.data(), p.c_str()) == 0, "publish metadata");
        sync_dir(p.parent_path());
    } catch (...) {
        unlink(b.data());
        throw;
    }
}
static void safe_name(const std::string &s) {
    require(!s.empty() && s.size() < 4096 && s.front() != '/' && s.find('\\') == std::string::npos &&
                s.find('\0') == std::string::npos && s.find_first_of("\n\r\t") == std::string::npos,
            "unsafe archive name");
    size_t start = 0;
    for (;;) {
        size_t end = s.find('/', start);
        std::string c = s.substr(start, end - start);
        require(!c.empty() && c != "." && c != "..", "unsafe archive component");
        if (end == std::string::npos)
            break;
        start = end + 1;
    }
}
static bool contained(const fs::path &a, const fs::path &b) {
    auto ia = a.begin(), ib = b.begin();
    for (; ia != a.end() && ib != b.end(); ++ia, ++ib)
        if (*ia != *ib)
            return false;
    return ia == a.end();
}
static void validate_base(const fs::path &base, AdmissionProgress *progress = nullptr,
                          const std::vector<AdmissionAssetCheck> &extra_checks = {}) {
    Json j = parse(read_file(base / "device.lock.json"));
    require(j.at("format").integer() == 1, "unsupported device lock schema", LT_UNSUPPORTED);
    require(!j.at("board").string().empty() && !j.at("build").string().empty(), "missing board/build");
    const Json &outputs = j.at("outputs");
    require(outputs.kind == Json::Object, "invalid outputs");
    const auto &nand = outputs.at("nand");
    require(nand.at("path").string() == "nand" && nand.at("listing_sha256").string().size() == 64,
            "missing authoritative NAND integrity record");
    auto required_file = [&](const std::string &key, const std::string &name) {
        const auto &r = outputs.at(key);
        require(r.at("path").string() == name && r.at("sha256").string().size() == 64,
                "missing authoritative " + name + " integrity record");
    };
    std::string board = j.at("board").string();
    if (board == "n72ap") {
        required_file("nor", "nor.bin");
        required_file("gid_blobs", "gid-blobs.bin");
        const auto *strategy = j.find("boot_strategy");
        if (!strategy || strategy->string() == "iboot")
            required_file("iboot", "iBoot.bin");
    }
    if (board == "n81ap") {
        required_file("nor", "nor.bin");
        required_file("kboot", "kboot.bin");
    }
    // Enumerate once, then hash a stable, sorted, checked file plan. Listing
    // strings and state/lock metadata are not source asset digest input bytes.
    AdmissionProgress quiet{nullptr, nullptr};
    auto &events = progress ? *progress : quiet;
    std::map<fs::path, uint64_t> assets;
    std::vector<AdmissionAssetCheck> checks = extra_checks;
    struct ListingCheck {
        std::vector<std::pair<std::string, fs::path>> files;
        std::string expected;
        uint64_t pages = 0;
        bool check_pages = false;
    };
    std::vector<ListingCheck> listings;
    auto add_asset = [&](const fs::path &path) {
        events.checkpoint();
        struct stat st {};
        io(lstat(path.c_str(), &st) == 0, "stat admission asset");
        require(S_ISREG(st.st_mode) && st.st_size >= 0, "admission asset must be a regular file");
        assets.emplace(path, uint64_t(st.st_size));
    };
    for (const auto &check : extra_checks)
        add_asset(check.path);
    for (const auto &pair : outputs.object) {
        events.checkpoint();
        const Json &o = pair.second;
        const Json *path = o.find("path");
        require(path, "output path missing");
        std::string name = path->string();
        safe_name(name);
        if (const Json *h = o.find("sha256")) {
            add_asset(base / name);
            checks.push_back({base / name, h->string(), "output checksum mismatch: " + name});
        }
        if (const Json *h = o.find("listing_sha256")) {
            require(fs::is_directory(fs::symlink_status(base / name)), "NAND is not a directory");
            std::vector<std::string> files;
            for (const auto &e : fs::recursive_directory_iterator(base / name)) {
                events.checkpoint();
                require(!e.is_symlink(), "symlink in NAND");
                if (e.is_regular_file())
                    files.push_back(e.path().lexically_relative(base / name).generic_string());
                else
                    require(e.is_directory(), "special file in NAND");
            }
            std::sort(files.begin(), files.end());
            ListingCheck listing;
            listing.expected = h->string();
            for (const auto &file : files) {
                safe_name(file);
                add_asset(base / name / file);
                listing.files.emplace_back(file, base / name / file);
            }
            if (const Json *p = o.find("pages")) {
                listing.pages = p->integer();
                listing.check_pages = true;
            }
            listings.push_back(std::move(listing));
        }
    }
    if (const Json *i = j.find("identity"))
        if (const Json *h = i->find("sha256")) {
            add_asset(base / "identity.json");
            checks.push_back({base / "identity.json", h->string(), "identity checksum mismatch"});
        }
    uint64_t total = 0;
    for (const auto &asset : assets) {
        require(asset.second <= UINT64_MAX - total, "admission byte count overflow");
        total += asset.second;
    }
    events.total = total;
    events.checkpoint();
    std::map<fs::path, std::string> digests;
    for (const auto &asset : assets) {
        events.checkpoint();
        auto fd = open_read(asset.first);
        digests.emplace(asset.first, digest_fd(fd.n, &events, asset.second));
    }
    for (const auto &check : checks) {
        events.checkpoint();
        require(digests.at(check.path) == check.sha256, check.message);
    }
    for (const auto &check : listings) {
        events.checkpoint();
        Sha256 listing;
        for (const auto &file : check.files) {
            events.checkpoint();
            std::string line = file.first + " " + digests.at(file.second) + "\n";
            listing.update(line.data(), line.size());
        }
        require(listing.finish() == check.expected, "NAND listing checksum mismatch");
        if (check.check_pages)
            require(check.files.size() == check.pages, "NAND page count mismatch");
    }
    events.checkpoint();
}
struct Stage {
    fs::path path, destination;
    bool published = false;
    explicit Stage(const char *dest) {
        require(dest && fs::path(dest).is_absolute(), "destination must be absolute");
        destination = fs::path(dest).lexically_normal();
        require(!fs::exists(fs::symlink_status(destination)), "destination already exists");
        auto parent = fs::canonical(destination.parent_path());
        destination = parent / destination.filename();
        std::string pattern = (parent / ".lt-import-XXXXXX").string();
        std::vector<char> b(pattern.begin(), pattern.end());
        b.push_back(0);
        io(mkdtemp(b.data()) != nullptr, "create staging");
        path = b.data();
    }
    ~Stage() {
        if (!published) {
            std::error_code ec;
            for (const auto &e : fs::recursive_directory_iterator(path, ec))
                if (e.is_directory())
                    chmod(e.path().c_str(), 0700);
            chmod(path.c_str(), 0700);
            fs::remove_all(path, ec);
        }
    }
    void finish() {
        validate_base(path);
        std::vector<fs::path> dirs;
        for (const auto &e : fs::recursive_directory_iterator(path)) {
            if (e.is_directory())
                dirs.push_back(e.path());
            else
                io(chmod(e.path().c_str(), 0400) == 0, "make base file immutable");
        }
        for (auto it = dirs.rbegin(); it != dirs.rend(); ++it) {
            sync_dir(*it);
            io(chmod(it->c_str(), 0500) == 0, "make base directory immutable");
        }
        sync_dir(path);
        io(chmod(path.c_str(), 0500) == 0, "make base immutable");
#ifdef SYS_renameat2
        io(syscall(SYS_renameat2, AT_FDCWD, path.c_str(), AT_FDCWD, destination.c_str(), RENAME_NOREPLACE) == 0,
           "publish base");
#else
        require(!fs::exists(destination), "destination race");
        io(rename(path.c_str(), destination.c_str()) == 0, "publish base");
#endif
        published = true;
        sync_dir(destination.parent_path());
    }
};
static uint64_t limit(const lt_import_config *c) {
    return c->max_bytes ? std::min<uint64_t>(c->max_bytes, 64ull << 30) : 64ull << 30;
}
static void progress(const lt_import_config *c, uint64_t written, uint64_t total) {
    if (c->progress)
        require(c->progress(c->opaque, written, total) == 0, "import cancelled", LT_CANCELLED);
}
static void copy_file(const fs::path &src, const fs::path &dst) {
    auto in = open_read(src);
    Fd out(open(dst.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600));
    io(out.n >= 0, "create copied file");
    std::array<char, 65536> b{};
    for (;;) {
        ssize_t n = read(in.n, b.data(), b.size());
        if (n < 0 && errno == EINTR)
            continue;
        io(n >= 0, "copy read");
        if (!n)
            break;
        write_all(out.n, b.data(), size_t(n));
    }
    io(fsync(out.n) == 0, "sync copied file");
}
std::string prepared_guest_package(lt_device *d) {
    const auto *outputs = d->lock.find("outputs");
    const auto *package = outputs ? outputs->find("guest_package") : nullptr;
    if (!package)
        return "";
    std::string name = package->at("path").string();
    safe_name(name);
    auto path = d->base / name;
    require(digest_file(path) == package->at("sha256").string(), "prepared guest package checksum mismatch");
    return path.string();
}
void clean_device(lt_device *d, bool confirmed) {
    d->dirty = !confirmed;
    if (confirmed) {
        std::error_code ec;
        fs::remove(d->state / ".dirty", ec);
        if (ec)
            throw Failure(LT_IO, "clear dirty: " + ec.message());
        sync_dir(d->state);
    }
}
} // namespace lt
namespace lt {
std::unique_ptr<lt_device> open_device_checked(const lt_device_config *c, lt_progress_fn observer, void *opaque,
                                               const std::vector<AdmissionAssetCheck> &extra_checks) {
    lt::abi(c);
    lt::require(c->base_path && c->state_path, "invalid device configuration");
    lt::require(lt::fs::path(c->base_path).is_absolute() && lt::fs::path(c->state_path).is_absolute(),
                "device paths must be absolute");
    auto d = std::make_unique<lt_device>();
    d->base = lt::fs::canonical(c->base_path);
    lt::fs::create_directories(c->state_path);
    d->state = lt::fs::canonical(c->state_path);
    lt::io(chmod(d->state.c_str(), 0700) == 0, "private device state permissions");
    lt::require(!lt::contained(d->base, d->state) && !lt::contained(d->state, d->base), "base/state overlap refused");
    d->lease = open((d->state / ".lease").c_str(), O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
    lt::io(d->lease >= 0, "open device lease");
    lt::require(flock(d->lease, LOCK_EX | LOCK_NB) == 0, "device has another writer", LT_BUSY);
    AdmissionProgress events{observer, opaque};
    events.checkpoint();
    lt::validate_base(d->base, &events, extra_checks);
    auto immutable = [](lt::fs::perms mode) {
        return (mode & (lt::fs::perms::owner_write | lt::fs::perms::group_write | lt::fs::perms::others_write)) ==
               lt::fs::perms::none;
    };
    lt::require(immutable(lt::fs::status(d->base).permissions()),
                "base root is writable: import into immutable storage first");
    events.checkpoint();
    for (const auto &entry : lt::fs::recursive_directory_iterator(d->base)) {
        events.checkpoint();
        lt::require(!entry.is_symlink(), "base symlink refused");
        auto mode = entry.status().permissions();
        lt::require((mode & (lt::fs::perms::owner_write | lt::fs::perms::group_write | lt::fs::perms::others_write)) ==
                        lt::fs::perms::none,
                    "base is writable: import it into immutable storage first");
    }
    events.checkpoint();
    d->lock = lt::parse(lt::read_file(d->base / "device.lock.json"));
    lt::require(d->lock.at("format").integer() == 1, "unsupported lock schema", LT_UNSUPPORTED);
    d->base_digest = lt::digest_file(d->base / "device.lock.json");
    events.checkpoint();
    auto pin = d->state / ".base-identity";
    if (lt::fs::exists(pin))
        lt::require(lt::read_file(pin, 128) == d->base_digest, "state belongs to another prepared base");
    else {
        for (const auto &entry : lt::fs::directory_iterator(d->state))
            lt::require(entry.path().filename() == ".lease", "unidentified nonempty device state");
        lt::atomic_write(pin, d->base_digest);
    }
    events.checkpoint();
    d->dirty = lt::fs::exists(d->state / ".dirty");
    lt::require(!d->dirty || c->allow_dirty, "interrupted session: reopen with explicit cold recovery; data retained",
                LT_DIRTY);
    events.checkpoint();
    lt::fs::create_directories(d->state / "nand");
    if (lt::fs::exists(d->base / "nor.bin") && !lt::fs::exists(d->state / "nor.bin")) {
        auto staged = d->state / ".nor-import";
        std::error_code ec;
        lt::fs::remove(staged, ec);
        lt::copy_file(d->base / "nor.bin", staged);
        lt::io(rename(staged.c_str(), (d->state / "nor.bin").c_str()) == 0, "publish writable NOR");
        lt::sync_dir(d->state);
    }
    events.checkpoint();
    if (lt::fs::exists(d->state / "nor.bin"))
        lt::require(lt::fs::is_regular_file(lt::fs::symlink_status(d->state / "nor.bin")) &&
                        lt::fs::file_size(d->state / "nor.bin") == lt::fs::file_size(d->base / "nor.bin"),
                    "writable NOR invalid or truncated");
    events.checkpoint();
    return d;
}
} // namespace lt
extern "C" {
uint32_t lt_abi_version(void) { return LT_ABI_VERSION; }
lt_status lt_sha256_file(const char *p, char h[65], lt_error *e) {
    return lt::guard(e, [&] {
        lt::require(p && h, "null path/output");
        std::string s = lt::digest_file(p);
        memcpy(h, s.c_str(), 65);
    });
}
lt_status lt_import_archive(const lt_import_config *c, lt_error *e) {
    return lt::guard(e, [&] {
        lt::abi(c);
        lt::require(c->source && c->expected_sha256 && strlen(c->expected_sha256) == 64,
                    "archive requires expected SHA256");
        auto input = lt::open_read(c->source);
        lt::require(lt::digest_fd(input.n) == c->expected_sha256, "archive SHA256 mismatch");
        lt::io(lseek(input.n, 0, SEEK_SET) == 0, "rewind archive");
        uint8_t h[12];
        lt::read_all(input.n, h, sizeof h);
        lt::require(memcmp(h, "ITPACK01", 8) == 0, "not an ITPACK01 prepared base");
        uint32_t size = uint32_t(h[8]) | (uint32_t(h[9]) << 8) | (uint32_t(h[10]) << 16) | (uint32_t(h[11]) << 24);
        lt::require(size > 0 && size <= 64u << 20, "invalid index bound");
        std::string index(size, '\0');
        lt::read_all(input.n, index.data(), index.size());
        lt::Json j = lt::parse(index);
        const auto &entries = j.at("entries");
        lt::require(entries.kind == lt::Json::Array && !entries.array.empty() && entries.array.size() <= 1000000,
                    "invalid entry count");
        uint64_t total = 0;
        std::set<std::string> names;
        for (const auto &entry : entries.array) {
            std::string name = entry.at("name").string();
            lt::safe_name(name);
            lt::require(names.insert(name).second, "duplicate archive entry");
            uint64_t n = entry.at("size").integer();
            lt::require(n <= lt::limit(c) && total <= lt::limit(c) - n, "archive exceeds resource limit");
            total += n;
            lt::require(entry.at("mode").integer() <= 0777, "invalid entry mode");
        }
        lt::require(names.count("device.lock.json"), "missing device lock");
        for (const auto &name : names) {
            for (size_t p = name.find('/'); p != std::string::npos; p = name.find('/', p + 1))
                lt::require(!names.count(name.substr(0, p)), "file/directory conflict");
        }
        lt::Stage stage(c->destination);
        z_stream z{};
        lt::require(inflateInit(&z) == Z_OK, "inflate initialization failed", LT_IO);
        struct Z {
            z_stream *z;
            ~Z() { inflateEnd(z); }
        } cleanup{&z};
        std::array<uint8_t, 65536> in{}, out{};
        int status = Z_OK;
        uint64_t written = 0;
        size_t next = 0;
        uint64_t remaining = 0;
        std::unique_ptr<lt::Fd> target;
        auto advance = [&] {
            while (remaining == 0 && next < entries.array.size()) {
                if (target) {
                    lt::io(fsync(target->n) == 0, "sync extracted file");
                    target.reset();
                }
                const auto &entry = entries.array[next++];
                auto path = stage.path / entry.at("name").string();
                lt::fs::create_directories(path.parent_path());
                target = std::make_unique<lt::Fd>(
                    open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600));
                lt::io(target->n >= 0, "create archive entry");
                remaining = entry.at("size").integer();
            }
            return remaining > 0;
        };
        while (status != Z_STREAM_END) {
            if (z.avail_in == 0) {
                ssize_t n = read(input.n, in.data(), in.size());
                lt::io(n > 0, "truncated compressed stream");
                z.next_in = in.data();
                z.avail_in = uInt(n);
            }
            z.next_out = out.data();
            z.avail_out = uInt(out.size());
            uInt before = z.avail_in;
            status = inflate(&z, Z_NO_FLUSH);
            lt::require(status == Z_OK || status == Z_STREAM_END, "corrupt zlib stream");
            size_t count = out.size() - z.avail_out, offset = 0;
            lt::require(count || before != z.avail_in || status == Z_STREAM_END, "stalled zlib stream");
            while (offset < count) {
                lt::require(advance(), "payload exceeds index");
                size_t take = size_t(std::min<uint64_t>(remaining, count - offset));
                lt::write_all(target->n, out.data() + offset, take);
                remaining -= take;
                offset += take;
                written += take;
            }
            lt::progress(c, written, total);
        }
        lt::require(z.avail_in == 0, "trailing archive payload");
        char extra;
        lt::require(read(input.n, &extra, 1) == 0, "trailing archive bytes");
        lt::require(!advance() && written == total, "payload shorter than index");
        if (target)
            lt::io(fsync(target->n) == 0, "sync final entry");
        target.reset();
        lt::progress(c, total, total);
        stage.finish();
    });
}
lt_status lt_import_directory(const lt_import_config *c, lt_error *e) {
    return lt::guard(e, [&] {
        lt::abi(c);
        lt::require(c->source && lt::fs::path(c->source).is_absolute(), "source must be absolute");
        lt::require(!lt::fs::is_symlink(lt::fs::symlink_status(c->source)), "source symlink refused");
        auto source = lt::fs::canonical(c->source);
        lt::Stage stage(c->destination);
        lt::require(!lt::contained(source, stage.path), "import destination inside source");
        uint64_t total = 0, written = 0;
        std::vector<lt::fs::path> files;
        for (const auto &entry : lt::fs::recursive_directory_iterator(source)) {
            lt::require(!entry.is_symlink(), "symlink source refused");
            if (entry.is_directory())
                continue;
            lt::require(entry.is_regular_file(), "special source file refused");
            uint64_t n = entry.file_size();
            lt::require(n <= lt::limit(c) && total <= lt::limit(c) - n, "directory exceeds limit");
            total += n;
            files.push_back(entry.path());
        }
        for (const auto &file : files) {
            auto relative = file.lexically_relative(source);
            lt::safe_name(relative.generic_string());
            auto dest = stage.path / relative;
            lt::fs::create_directories(dest.parent_path());
            lt::copy_file(file, dest);
            written += lt::fs::file_size(file);
            lt::progress(c, written, total);
        }
        stage.finish();
    });
}
lt_status lt_device_open(const lt_device_config *c, lt_device **out, lt_error *e) {
    return lt_device_open_observed(c, nullptr, nullptr, out, e);
}
lt_status lt_device_open_observed(const lt_device_config *c, lt_progress_fn observer, void *opaque, lt_device **out,
                                  lt_error *e) {
    if (out)
        *out = nullptr;
    return lt::guard(e, [&] {
        lt::require(out, "device output missing");
        *out = lt::open_device_checked(c, observer, opaque, {}).release();
    });
}
lt_status lt_device_describe(lt_device *d, lt_device_info *out, lt_error *e) {
    return lt::guard(e, [&] {
        lt::require(d, "null device");
        lt::abi(out);
        snprintf(out->board, sizeof out->board, "%s", d->lock.at("board").string().c_str());
        snprintf(out->build, sizeof out->build, "%s", d->lock.at("build").string().c_str());
        const auto *s = d->lock.find("boot_strategy");
        snprintf(out->boot_strategy, sizeof out->boot_strategy, "%s", s ? s->string().c_str() : "");
        snprintf(out->base_sha256, sizeof out->base_sha256, "%s", d->base_digest.c_str());
        out->dirty = d->dirty;
    });
}
void lt_device_close(lt_device *d) {
    if (d && !d->active)
        delete d;
}
lt_status lt_device_boot_argv(lt_device *d, const lt_boot_config *c, int *argc, const char *const **argv, lt_error *e) {
    return lt::guard(e, [&] {
        lt::require(d && argc && argv, "null boot output");
        lt::abi(c);
        lt::require(!d->active, "device running", LT_BUSY);
        lt::require(c->machine && *c->machine, "engine machine facts required");
        std::string board = d->lock.at("board").string();
        bool n72 = board == "n72ap", kboot = board == "n81ap";
        lt::require(n72 || kboot, "native boot routes currently n72ap and n81ap only", LT_UNSUPPORTED);
        std::string strategy =
            d->lock.find("boot_strategy") ? d->lock.at("boot_strategy").string() : (n72 ? "iboot" : "kboot");
        lt::require((n72 && (strategy == "iboot" || strategy == "bootrom")) || (kboot && strategy == "kboot"),
                    "unsupported board boot strategy", LT_UNSUPPORTED);
        if (n72)
            lt::require(c->bootrom_path && lt::fs::is_regular_file(c->bootrom_path), "n72 boot ROM missing");
        for (const char *p : {"nand", "nor.bin"})
            lt::require(lt::fs::exists(d->base / p), std::string("base missing ") + p);
        if (n72) {
            lt::require(lt::fs::exists(d->base / "gid-blobs.bin"), "GID blobs missing");
            if (strategy == "iboot")
                lt::require(lt::fs::exists(d->base / "iBoot.bin"), "base iBoot missing");
        } else
            lt::require(lt::fs::is_regular_file(d->base / "kboot.bin"), "prepared kboot.bin missing");
        auto escape = [](const std::string &s) {
            std::string o;
            for (char b : s) {
                lt::require(b != '\0', "embedded null");
                o += b;
                if (b == ',')
                    o += ',';
            }
            return o;
        };
        std::string machine = c->machine;
        auto option = [&](const std::string &k, const std::string &v) { machine += "," + k + "=" + escape(v); };
        if (n72) {
            option("boot-args", "amfi_allow_any_signature=1 cs_enforcement_disable=1");
            option("direct-iboot", strategy == "iboot" ? (d->base / "iBoot.bin").string() : "");
            option("direct-llb", "");
            option("bootrom", c->bootrom_path);
            option("nor", (d->base / "nor.bin").string());
            option("nandrw", (d->state / "nand").string());
            option("gid-blobs", (d->base / "gid-blobs.bin").string());
            option("wifi", "on");
        } else {
            option("kboot", (d->base / "kboot.bin").string());
            option("nand-overlay", (d->state / "nand").string());
            if (lt::fs::exists(d->base / "identity.json")) {
                auto identity = lt::parse(lt::read_file(d->base / "identity.json"));
                if (const auto *die = identity.find("die-id")) {
                    lt::require(die->kind == lt::Json::Array, "invalid die-id");
                    std::string value;
                    for (const auto &word : die->array) {
                        if (!value.empty())
                            value += ":";
                        value += word.string();
                    }
                    option("die-id", value);
                }
            }
        }
        option("nand", (d->base / "nand").string());
        option("nor-rw", (d->state / "nor.bin").string());
        std::string package =
            c->guest_package_path && *c->guest_package_path ? c->guest_package_path : lt::prepared_guest_package(d);
        if (!package.empty())
            option("guest-package", lt::guest_offer(d, package.c_str()));
        if (const auto *m = d->lock.find("machine")) {
            lt::require(m->kind == lt::Json::Object, "invalid machine metadata");
            for (const auto &v : m->object) {
                lt::require(v.first.find_first_not_of("abcdefghijklmnopqrstuvwxyz0123456789-") == std::string::npos,
                            "invalid machine option key");
                lt::require(v.second.kind == lt::Json::String || v.second.kind == lt::Json::Number ||
                                v.second.kind == lt::Json::Boolean,
                            "invalid machine option value");
                static const std::set<std::string> safe_options = {"wifi-mac", "bt-mac", "ecid", "aes-uid",
                                                                   "rtc-epoch"};
                lt::require(safe_options.count(v.first), "unsupported prepared machine property: " + v.first,
                            LT_UNSUPPORTED);
                option(v.first,
                       v.second.kind == lt::Json::Boolean ? (v.second.text == "true" ? "on" : "off") : v.second.text);
            }
        }
        d->args = {"LightTouch", "-M", machine};
        if (n72) {
            d->args.push_back("-m");
            d->args.push_back("128M");
        }
        d->args.insert(d->args.end(),
                       {"-display", "none", "-no-shutdown", "-audio", "driver=none", "-serial",
                        c->serial_path && *c->serial_path ? std::string("file:") + c->serial_path : "null"});
        d->argv.clear();
        for (const auto &s : d->args)
            d->argv.push_back(s.c_str());
        *argc = int(d->argv.size());
        *argv = d->argv.data();
    });
}
}
lt_device::~lt_device() {
    if (lease >= 0)
        close(lease);
}

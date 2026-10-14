#pragma once
#include "lighttouch.h"
#include "Json.hpp"
#include "Sha256.hpp"
#include <filesystem>
#include <atomic>
#include <stdexcept>
#include <string>
#include <vector>
#include <cstdio>
#include <cerrno>
#include <cstring>
namespace lt {
namespace fs = std::filesystem;
struct Failure : std::runtime_error {
    lt_status status;
    Failure(lt_status s, const std::string &m) : std::runtime_error(m), status(s) {}
};
inline void require(bool b, const std::string &m, lt_status s = LT_INVALID) {
    if (!b)
        throw Failure(s, m);
}
inline void io(bool b, const std::string &m) { require(b, m + ": " + std::strerror(errno), LT_IO); }
template <class T> void abi(const T *p) {
    require(p && p->size >= sizeof(T) && p->version == LT_ABI_VERSION, "invalid ABI size/version");
}
inline lt_status error(lt_error *e, lt_status s, const std::string &m) {
    if (e && e->size >= sizeof(*e) && e->version == LT_ABI_VERSION) {
        e->code = s;
        snprintf(e->message, sizeof(e->message), "%s", m.c_str());
    }
    return s;
}
template <class F> lt_status guard(lt_error *e, F f) {
    try {
        f();
        return error(e, LT_OK, "");
    } catch (const Failure &x) {
        return error(e, x.status, x.what());
    } catch (const std::exception &x) {
        return error(e, LT_INVALID, x.what());
    }
}
std::string read_file(const fs::path &, size_t limit = 64u << 20);
std::string digest_file(const fs::path &);
void atomic_write(const fs::path &, const std::string &);
void sync_dir(const fs::path &);
std::string guest_offer(lt_device *, const char *);
void clean_device(lt_device *, bool);
} // namespace lt
struct lt_device {
    std::filesystem::path base, state;
    int lease = -1;
    lt::Json lock;
    std::atomic<bool> dirty{false}, active{false};
    std::string base_digest;
    std::vector<std::string> args;
    std::vector<const char *> argv;
    ~lt_device();
};

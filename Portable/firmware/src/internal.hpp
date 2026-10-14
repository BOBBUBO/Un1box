#pragma once
#include "json.hpp"
#include "lighttouch_firmware.h"
#include <algorithm>
#include <filesystem>
#include <map>
#include <stdexcept>
#include <vector>
namespace ltf {
using Bytes = std::vector<uint8_t>;
using Json = nlohmann::json;
namespace fs = std::filesystem;
struct Error : std::runtime_error {
    lt_fw_status status;
    Error(lt_fw_status s, const std::string &m) : runtime_error(m), status(s) {}
};
inline void require(bool b, const std::string &m) {
    if (!b)
        throw Error(LT_FW_FORMAT, m);
}
uint64_t number(const Bytes &, size_t, size_t, bool = false);
void put(Bytes &, size_t, uint64_t, size_t = 4, bool = false);
Bytes read(const fs::path &, size_t = 128 * 1024 * 1024);
void write(const fs::path &, const Bytes &);
std::string digest(const Bytes &, bool = false);
Bytes hex(std::string);
Bytes aes(const Bytes &, const Bytes &, const Bytes &, bool = true);
Json plist(const Bytes &);
Bytes xml(const Json &);
Bytes decrypt_img3(const Bytes &, const std::string &, const std::string &, bool = false);
struct Tag {
    size_t offset, length, total;
};
std::map<std::string, Tag> tags(const Bytes &);
Bytes nor(const Json &, const std::map<std::string, Bytes> &, const std::vector<std::string> &);
std::map<std::pair<int, int>, Bytes> metadata(int, int);
std::pair<int, int> page(int);
Bytes resign(Bytes);
void grow_hfsx(const fs::path &, uint32_t);
inline Bytes bytes(const std::string &s) { return Bytes(s.begin(), s.end()); }
} // namespace ltf

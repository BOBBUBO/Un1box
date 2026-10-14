#include "internal.hpp"
#include "tinyxml2.h"
#include <openssl/evp.h>
#include <zlib.h>
#include <fstream>
#include <functional>
#include <set>
namespace ltf {
uint64_t number(const Bytes &b, size_t p, size_t n, bool big) {
    require(n <= 8 && p <= b.size() && n <= b.size() - p, "truncated integer");
    uint64_t v = 0;
    for (size_t i = 0; i < n; i++)
        v |= uint64_t(b[p + i]) << ((big ? n - 1 - i : i) * 8);
    return v;
}
void put(Bytes &b, size_t p, uint64_t v, size_t n, bool big) {
    require(p <= b.size() && n <= b.size() - p, "write bounds");
    for (size_t i = 0; i < n; i++)
        b[p + i] = v >> ((big ? n - 1 - i : i) * 8);
}
Bytes read(const fs::path &p, size_t limit) {
    std::ifstream f(p, std::ios::binary);
    if (!f)
        throw Error(LT_FW_IO, "cannot read " + p.string());
    f.seekg(0, std::ios::end);
    auto n = f.tellg();
    require(n >= 0 && uint64_t(n) <= limit, "oversized input " + p.string());
    f.seekg(0);
    Bytes b(n);
    if (n && !f.read((char *)b.data(), n))
        throw Error(LT_FW_IO, "short read");
    return b;
}
void write(const fs::path &p, const Bytes &b) {
    std::ofstream f(p, std::ios::binary | std::ios::trunc);
    if (!f || (!b.empty() && !f.write((const char *)b.data(), b.size())))
        throw Error(LT_FW_IO, "cannot write " + p.string());
    f.close();
    if (!f)
        throw Error(LT_FW_IO, "close failed");
}
std::string digest(const Bytes &b, bool sha1) {
    unsigned char h[64];
    unsigned n = 0;
    require(EVP_Digest(b.data(), b.size(), h, &n, sha1 ? EVP_sha1() : EVP_sha256(), nullptr) == 1, "digest failure");
    std::string s;
    for (unsigned i = 0; i < n; i++) {
        s += "0123456789abcdef"[h[i] >> 4];
        s += "0123456789abcdef"[h[i] & 15];
    }
    return s;
}
Bytes hex(std::string s) {
    if (s.size() % 2)
        s = "0" + s;
    Bytes b;
    for (size_t i = 0; i < s.size(); i += 2) {
        auto digit = [](char c) {
            if (c >= '0' && c <= '9')
                return c - '0';
            if (c >= 'a' && c <= 'f')
                return c - 'a' + 10;
            if (c >= 'A' && c <= 'F')
                return c - 'A' + 10;
            throw Error(LT_FW_FORMAT, "bad hex");
        };
        b.push_back(digit(s[i]) * 16 + digit(s[i + 1]));
    }
    return b;
}
Bytes aes(const Bytes &b, const Bytes &key, const Bytes &iv, bool dec) {
    require((key.size() == 16 || key.size() == 32) && iv.size() == 16 && b.size() % 16 == 0, "AES geometry");
    EVP_CIPHER_CTX *c = EVP_CIPHER_CTX_new();
    require(c, "AES allocation");
    Bytes out(b.size() + 16);
    int n = 0, last = 0;
    int ok = EVP_CipherInit_ex(c, key.size() == 16 ? EVP_aes_128_cbc() : EVP_aes_256_cbc(), nullptr, key.data(),
                               iv.data(), !dec) &&
             EVP_CIPHER_CTX_set_padding(c, 0) && EVP_CipherUpdate(c, out.data(), &n, b.data(), b.size()) &&
             EVP_CipherFinal_ex(c, out.data() + n, &last);
    EVP_CIPHER_CTX_free(c);
    require(ok, "AES failure");
    out.resize(n + last);
    return out;
}
std::map<std::string, Tag> tags(const Bytes &b) {
    require(b.size() >= 20 && number(b, 0, 4) == 0x496d6733, "not IMG3");
    auto full = number(b, 4, 4);
    require(full >= 20 && full <= b.size(), "IMG3 bounds");
    std::map<std::string, Tag> out;
    for (size_t p = 20; p < full;) {
        require(full - p >= 12, "IMG3 tag header");
        size_t n = number(b, p + 4, 4), len = number(b, p + 8, 4);
        require(n >= 12 && n <= full - p && len <= n - 12, "IMG3 tag extent");
        std::string name(b.begin() + p, b.begin() + p + 4);
        std::reverse(name.begin(), name.end());
        require(out.emplace(name, Tag{p, len, n}).second, "duplicate IMG3 tag");
        p += n;
    }
    return out;
}
Bytes decrypt_img3(const Bytes &b, const std::string &key, const std::string &iv, bool tail) {
    auto t = tags(b).at("DATA");
    size_t n = tail ? t.length & ~size_t(15) : (t.length + 15) & ~size_t(15);
    require(n <= t.total - 12, "IMG3 padding");
    auto out = aes(Bytes(b.begin() + t.offset + 12, b.begin() + t.offset + 12 + n), hex(key), hex(iv));
    out.resize(t.length);
    if (tail)
        std::copy(b.begin() + t.offset + 12 + n, b.begin() + t.offset + 12 + t.length, out.begin() + n);
    return out;
}
Json plist(const Bytes &b) {
    if (b.size() >= 8 && std::string(b.begin(), b.begin() + 8) == "bplist00") {
        require(b.size() >= 40, "binary plist trailer");
        size_t t = b.size() - 32, os = b[t + 6], rs = b[t + 7];
        uint64_t count = number(b, t + 8, 8, true), root = number(b, t + 16, 8, true),
                 table = number(b, t + 24, 8, true);
        require(os >= 1 && os <= 8 && rs >= 1 && rs <= 8 && count <= 100000 && root < count && table <= t &&
                    count <= (t - table) / os,
                "binary plist geometry");
        std::set<uint64_t> active;
        std::function<Json(uint64_t, int)> obj = [&](uint64_t id, int depth) -> Json {
            require(id < count && depth < 64 && active.insert(id).second, "plist cycle/bounds");
            struct Pop {
                std::set<uint64_t> &s;
                uint64_t i;
                ~Pop() { s.erase(i); }
            } pop{active, id};
            size_t p = number(b, table + id * os, os, true);
            require(p >= 8 && p < t, "plist object offset");
            int kind = b[p] >> 4;
            uint64_t len = b[p++] & 15;
            if (kind != 1 && len == 15) {
                require(p < t && (b[p] >> 4) == 1 && (b[p] & 15) <= 3, "plist length");
                size_t n = 1u << (b[p++] & 15);
                len = number(b, p, n, true);
                p += n;
            }
            require(len <= 128 * 1024 * 1024, "plist object length");
            switch (kind) {
            case 0:
                require(len == 8 || len == 9, "plist scalar");
                return len == 9;
            case 1:
                require(len <= 3, "plist integer width");
                return number(b, p, 1u << len, true);
            case 5:
                require(p <= t && len <= t - p, "plist string");
                return std::string(b.begin() + p, b.begin() + p + len);
            case 6: {
                require(p <= t && len <= (t - p) / 2, "plist UTF16");
                std::string s;
                for (uint64_t i = 0; i < len; i++) {
                    uint32_t c = number(b, p + i * 2, 2, true);
                    require(c < 0xd800 || c > 0xdfff, "unsupported plist surrogate");
                    if (c < 128)
                        s += char(c);
                    else if (c < 2048) {
                        s += char(0xc0 | (c >> 6));
                        s += char(0x80 | (c & 63));
                    } else {
                        s += char(0xe0 | (c >> 12));
                        s += char(0x80 | ((c >> 6) & 63));
                        s += char(0x80 | (c & 63));
                    }
                }
                return s;
            }
            case 10: {
                require(p <= t && len <= (t - p) / rs, "plist array");
                Json a = Json::array();
                for (uint64_t i = 0; i < len; i++)
                    a.push_back(obj(number(b, p + i * rs, rs, true), depth + 1));
                return a;
            }
            case 13: {
                require(p <= t && len <= (t - p) / rs / 2, "plist dict");
                Json a = Json::object();
                for (uint64_t i = 0; i < len; i++) {
                    auto k = obj(number(b, p + i * rs, rs, true), depth + 1);
                    require(k.is_string() && !a.contains(k.get<std::string>()), "plist dict key");
                    a[k.get<std::string>()] = obj(number(b, p + (len + i) * rs, rs, true), depth + 1);
                }
                return a;
            }
            default:
                throw Error(LT_FW_FORMAT, "unsupported binary plist value");
            }
        };
        return obj(root, 0);
    }
    tinyxml2::XMLDocument doc;
    require(doc.Parse((const char *)b.data(), b.size()) == tinyxml2::XML_SUCCESS, "invalid XML plist");
    auto root = doc.FirstChildElement("plist");
    require(root, "missing plist");
    std::function<Json(tinyxml2::XMLElement *, int)> parse = [&](tinyxml2::XMLElement *e, int d) -> Json {
        require(e && d < 64, "plist nesting");
        std::string tag = e->Name(), text = e->GetText() ? e->GetText() : "";
        if (tag == "dict") {
            Json j = Json::object();
            for (auto k = e->FirstChildElement(); k; k = k->NextSiblingElement()->NextSiblingElement()) {
                require(std::string(k->Name()) == "key" && k->GetText() && k->NextSiblingElement(), "plist dict pair");
                std::string key = k->GetText();
                require(!j.contains(key), "duplicate plist key");
                j[key] = parse(k->NextSiblingElement(), d + 1);
            }
            return j;
        }
        if (tag == "array") {
            Json j = Json::array();
            for (auto v = e->FirstChildElement(); v; v = v->NextSiblingElement())
                j.push_back(parse(v, d + 1));
            return j;
        }
        if (tag == "string")
            return text;
        if (tag == "true")
            return true;
        if (tag == "false")
            return false;
        if (tag == "integer")
            return std::stoll(text);
        if (tag == "real")
            return std::stod(text);
        throw Error(LT_FW_FORMAT, "unsupported XML plist value " + tag);
    };
    return parse(root->FirstChildElement(), 0);
}
Bytes xml(const Json &j) {
    tinyxml2::XMLDocument doc;
    doc.InsertEndChild(doc.NewDeclaration());
    auto root = doc.NewElement("plist");
    root->SetAttribute("version", "1.0");
    doc.InsertEndChild(root);
    std::function<tinyxml2::XMLElement *(const Json &)> value = [&](const Json &v) {
        auto e = doc.NewElement(v.is_object()           ? "dict"
                                : v.is_array()          ? "array"
                                : v.is_boolean()        ? (v.get<bool>() ? "true" : "false")
                                : v.is_number_integer() ? "integer"
                                : v.is_number_float()   ? "real"
                                                        : "string");
        if (v.is_object())
            for (auto it = v.begin(); it != v.end(); ++it) {
                auto k = doc.NewElement("key");
                k->SetText(it.key().c_str());
                e->InsertEndChild(k);
                e->InsertEndChild(value(it.value()));
            }
        else if (v.is_array())
            for (auto &a : v)
                e->InsertEndChild(value(a));
        else if (!v.is_boolean())
            e->SetText((v.is_string() ? v.get<std::string>() : v.dump()).c_str());
        return e;
    };
    root->InsertEndChild(value(j));
    tinyxml2::XMLPrinter p;
    doc.Print(&p);
    return bytes(p.CStr());
}
static Bytes chrp(int sig, int len, const std::string &name) {
    Bytes b(16);
    b[0] = sig;
    put(b, 2, len / 16, 2);
    require(name.size() <= 12, "CHRP name");
    std::copy(name.begin(), name.end(), b.begin() + 4);
    int sum = b[0];
    for (int i = 2; i < 16; i++) {
        sum += b[i];
        if (sum > 255)
            sum = (sum & 255) + 1;
    }
    b[1] = sum;
    return b;
}
Bytes nor(const Json &id, const std::map<std::string, Bytes> &images, const std::vector<std::string> &types) {
    Bytes n(0x100000);
    auto copy = [&](size_t o, const Bytes &b) {
        require(o <= n.size() && b.size() <= n.size() - o, "NOR capacity");
        std::copy(b.begin(), b.end(), n.begin() + o);
    };
    copy(0, bytes("2GMI"));
    put(n, 4, 0x40);
    put(n, 12, 0x200);
    copy(0x4000, bytes("gfCS"));
    int fields[] = {0xc8, 0x2000, 0x10001, 0, 4};
    for (int i = 0; i < 5; i++)
        put(n, 0x4004 + i * 4, fields[i]);
    std::pair<std::string, std::string> rec[] = {
        {"Mod#", "model-number"}, {"Regn", "region-info"}, {"SrNm", "serial-number"}, {"Batt", "battery-serial"}};
    for (int i = 0; i < 4; i++) {
        std::string t = rec[i].first, s = id.at(rec[i].second);
        require(s.size() <= 16, "SysCfg value");
        std::reverse(t.begin(), t.end());
        copy(0x4018 + i * 20, bytes(t));
        copy(0x401c + i * 20, bytes(s));
    }
    Bytes bank(0x2000);
    auto cp = [&](size_t p, const Bytes &b) { std::copy(b.begin(), b.end(), bank.begin() + p); };
    cp(0, chrp(0x5a, 0x20, "nvram"));
    put(bank, 0x14, 0x10);
    cp(0x20, chrp(0x70, 0x800, "common"));
    std::string nv = "debug-uarts=1";
    nv += '\0';
    for (auto k : {"bt-mac", "wifi-mac"}) {
        std::string s = id.at(k);
        std::transform(s.begin(), s.end(), s.begin(), ::toupper);
        nv += (std::string(k) == "bt-mac" ? "btaddr=" : "wifiaddr=") + s;
        nv += '\0';
    }
    cp(0x30, bytes(nv));
    cp(0x820, chrp(0xa1, 0x810, "APL,OSXPanic"));
    cp(0x1030, chrp(0x7f, 0x2000 - 0x1030, std::string(12, 'w')));
    put(bank, 0x10, adler32(1, bank.data() + 0x14, bank.size() - 0x14));
    copy(0xfc000, bank);
    auto wrap = aes(hex("db1f5b33606c5f1c1934aa66589c0661"), hex("0123456789abcdef0123456789abcdef"), Bytes(16));
    size_t p = 0x8000;
    for (auto &type : types) {
        auto img = images.at(type);
        auto tag = tags(img).at("SHSH");
        require(tag.length % 16 == 0, "NOR signature alignment");
        auto sig = aes(Bytes(img.begin() + tag.offset + 12, img.begin() + tag.offset + 12 + tag.length), wrap,
                       Bytes(16), false);
        std::copy(sig.begin(), sig.end(), img.begin() + tag.offset + 12);
        size_t padded = (img.size() + 63) & ~size_t(63);
        require(padded <= 0xfc000 - p, "NOR overflow");
        put(img, 4, padded);
        copy(p, img);
        p += padded;
    }
    put(n, 0x10, (p + 63) / 64);
    put(n, 0x30, crc32(0, n.data(), 0x30));
    return n;
}
std::pair<int, int> page(int b) {
    require(b >= 0 && b < 1835008, "NAND block");
    int r = (b + 3) / 4;
    return {(b + 3) % 4, (2 * (r / 256) + 2 + r % 2) * 128 + (r % 256) / 2};
}
std::map<std::pair<int, int>, Bytes> metadata(int blocks, int epoch) {
    require(blocks > 0 && blocks <= 1835008 && epoch >= 1 && epoch <= 9, "NAND geometry/epoch");
    std::map<std::pair<int, int>, Bytes> m;
    auto rec = [&](int cs, int pg, Bytes data, int spare) {
        data.resize(4160);
        if (spare == 1)
            data[4105] = 0x43;
        else if (spare == 2) {
            data[4096] = 1;
            data[4105] = 0x80;
        } else if (spare == 3) {
            data[4104] = 255;
            data[4106] = 255;
        }
        m[{cs, pg}] = data;
    };
    auto words = [](const std::vector<int> &w) {
        Bytes b(4096);
        for (size_t i = 0; i < w.size(); i++)
            put(b, i * 2, w[i], 2);
        return b;
    };
    rec(0, 0, Bytes(4096), 1);
    std::vector<int> w(2048);
    w[4] = 20;
    for (int i = 0; i < 20; i++)
        w[7 + i] = 3 + i;
    for (int i = 0; i < 18; i++) {
        w[28 + i * 2] = 5 + i;
        w[212 + i * 10] = 65535;
    }
    w[1021] = 0x4656;
    w[1022] = 65535;
    w[1023] = 0xb9a9;
    auto ctx = words(w);
    auto msg = bytes("Writing FTL Meta to physical page 255 @ cs 3\nto physical page 130 @ cs 2\n");
    std::copy(msg.begin(), msg.end(), ctx.begin() + 2048);
    rec(3, 255, ctx, 1);
    std::set<std::pair<int, int>> maps;
    for (int k = 0; k < 18; k++) {
        int x = k + 1, r = x / 4;
        auto p = std::make_pair(x % 4, ((r + 1) % 2) * 128 + (r + 1) / 2);
        maps.insert(p);
        for (int i = 0; i < 2048; i++)
            w[i] = k * 2048 + i + 1;
        rec(p.first, p.second, words(w), 0);
    }
    Bytes bbt(4096, 255);
    std::string sig = "DEVICEINFOBBT";
    std::copy(sig.begin(), sig.end(), bbt.begin());
    std::fill(bbt.begin() + 13, bbt.begin() + 16, 0);
    for (int cs = 0; cs < 4; cs++) {
        for (int pg = 128; pg < 136; pg++)
            if (!maps.count({cs, pg})) {
                w.assign(2048, 0);
                w[0] = cs;
                w[6] = 1;
                std::fill(w.begin() + 19, w.begin() + 839, 0xfff0);
                int tail[] = {0, 1, 2, 3, 0x800, 0x800, 0, 1, 2};
                std::copy(tail, tail + 9, w.begin() + 839);
                w[877] = 0x14;
                w[878] = 0x10;
                w[1018] = 2;
                if (cs == 0 && pg > 128) {
                    w[1024] = 1;
                    w[1028] = 0x8000;
                }
                auto b = words(w);
                uint32_t sum = 0, x = 0;
                for (int i = 0; i < 510; i++) {
                    uint32_t v = number(b, i * 4, 4);
                    sum += v;
                    x ^= v;
                }
                put(b, 0x7f8, sum + 0xaabbccdd);
                put(b, 0x7fc, x ^ 0xaabbccdd);
                rec(cs, pg, b, 2);
            }
        rec(cs, 4095 * 128 + 1, bbt, 0);
    }
    Bytes mbr(4096);
    mbr[8] = 255;
    mbr[10] = 255;
    mbr[0x1c2] = 0xee;
    put(mbr, 0x1c6, 3);
    put(mbr, 0x1ca, blocks + 10);
    mbr[510] = 0x55;
    mbr[511] = 0xaa;
    Bytes ent(4096);
    auto uuid = hex("005346480000aa11aa1100306543ecac");
    std::copy(uuid.begin(), uuid.end(), ent.begin());
    put(ent, 0x20, 3, 8);
    put(ent, 0x28, blocks + 2, 8);
    Bytes hdr(4096);
    sig = "EFI PART";
    std::copy(sig.begin(), sig.end(), hdr.begin());
    put(hdr, 8, 0x10000);
    put(hdr, 12, 0x5c);
    put(hdr, 0x48, 2, 8);
    put(hdr, 0x50, 1);
    put(hdr, 0x54, 128);
    put(hdr, 0x58, crc32(0, ent.data(), 128));
    put(hdr, 0x10, crc32(0, hdr.data(), 0x5c));
    rec(0, 256, mbr, 3);
    rec(1, 256, hdr, 3);
    rec(2, 256, ent, 3);
    Bytes sign(4096);
    sig = "NANDDRIVERSIGN";
    std::copy(sig.begin(), sig.end(), sign.begin());
    put(sign, 0x34, 4);
    put(sign, 0x38, 0x43313100 | (0x30 + epoch));
    rec(0, 4095 * 128, sign, 0);
    return m;
}
Bytes resign(Bytes b) {
    auto le = [&](size_t p) { return number(b, p, 4); };
    auto be = [&](size_t p) { return number(b, p, 4, true); };
    require(b.size() >= 28 && le(0) == 0xfeedface && le(4) == 12, "signature Mach-O");
    size_t count = le(16), sz = le(20), p = 28, start = 0, allocated = 0;
    require(sz <= b.size() - 28 && count <= sz / 8, "load commands");
    for (size_t i = 0; i < count; i++) {
        require(p <= 28 + sz - 8, "load command header");
        size_t n = le(p + 4);
        require(n >= 8 && n <= 28 + sz - p, "load command length");
        if (le(p) == 0x1d) {
            require(n >= 16 && !start, "signature command");
            start = le(p + 8);
            allocated = le(p + 12);
        }
        p += n;
    }
    require(start && start <= b.size() && allocated >= 12 && allocated <= b.size() - start && be(start) == 0xfade0cc0,
            "signature superblob");
    size_t len = be(start + 4), blobs = be(start + 8), dirs = 0;
    require(len >= 12 && len <= allocated && blobs <= (len - 12) / 8, "signature index");
    std::vector<std::pair<uint32_t, uint32_t>> entries;
    for (size_t i = 0; i < blobs; i++) {
        uint32_t type = be(start + 12 + i * 8), off = be(start + 16 + i * 8);
        require(off >= 12 + blobs * 8 && off <= len - 8, "signature offset");
        size_t q = start + off, n = be(q + 4);
        require(n >= 8 && n <= len - off, "signature blob length");
        if (type == 0 || (type >= 0x1000 && type <= 0x1005)) {
            require(n >= 44 && be(q) == 0xfade0c02, "code directory");
            size_t hash = be(q + 16), special = be(q + 24), slots = be(q + 28), limit = be(q + 32);
            int hs = b[q + 36], ht = b[q + 37], exp = b[q + 39];
            require(exp >= 1 && exp <= 20 && limit && limit <= start &&
                        ((ht == 1 && hs == 20) || (ht == 2 && hs == 32)) && hash >= 44 + special * hs && hash <= n &&
                        slots <= (n - hash) / hs,
                    "code directory geometry");
            size_t page = 1u << exp;
            require(slots == (limit + page - 1) / page, "code slots");
            require(be(q + 8) < 0x20100 || (n >= 48 && be(q + 44) == 0), "scatter signature unsupported");
            put(b, q + 12, be(q + 12) | 2, 4, true);
            for (size_t k = 0; k < slots; k++) {
                auto h = hex(digest(Bytes(b.begin() + k * page, b.begin() + std::min(limit, (k + 1) * page)), ht == 1));
                std::copy(h.begin(), h.end(), b.begin() + q + hash + k * hs);
            }
            dirs++;
        }
        if (type != 0x10000)
            entries.emplace_back(type, off);
    }
    require(dirs && std::any_of(entries.begin(), entries.end(), [](auto &e) { return e.first == 0; }),
            "missing primary code directory");
    for (size_t i = 0; i < blobs; i++) {
        put(b, start + 12 + i * 8, i < entries.size() ? entries[i].first : 0, 4, true);
        put(b, start + 16 + i * 8, i < entries.size() ? entries[i].second : 0, 4, true);
    }
    put(b, start + 8, entries.size(), 4, true);
    return b;
}
} // namespace ltf

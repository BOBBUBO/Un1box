#pragma once
#include <cstdint>
#include <cstring>
#include <string>
namespace lt {
// FIPS 180-4 SHA-256; streaming so NAND/import never needs a whole image in RAM.
class Sha256 {
    uint32_t h[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    uint8_t block[64]{};
    uint64_t length = 0;
    size_t used = 0;
    static uint32_t r(uint32_t n, unsigned b) { return (n >> b) | (n << (32 - b)); }
    void compress() {
        static const uint32_t k[64] = {
            0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
            0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
            0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
            0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
            0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
            0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
            0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
            0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};
        uint32_t w[64];
        for (int i = 0; i < 16; i++)
            w[i] = (uint32_t(block[i * 4]) << 24) | (uint32_t(block[i * 4 + 1]) << 16) |
                   (uint32_t(block[i * 4 + 2]) << 8) | block[i * 4 + 3];
        for (int i = 16; i < 64; i++)
            w[i] = w[i - 16] + (r(w[i - 15], 7) ^ r(w[i - 15], 18) ^ (w[i - 15] >> 3)) + w[i - 7] +
                   (r(w[i - 2], 17) ^ r(w[i - 2], 19) ^ (w[i - 2] >> 10));
        uint32_t a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], v = h[7];
        for (int i = 0; i < 64; i++) {
            uint32_t t = v + (r(e, 6) ^ r(e, 11) ^ r(e, 25)) + ((e & f) ^ (~e & g)) + k[i] + w[i];
            v = g;
            g = f;
            f = e;
            e = d + t;
            d = c;
            c = b;
            b = a;
            a = t + (r(b, 2) ^ r(b, 13) ^ r(b, 22)) + ((b & c) ^ (b & d) ^ (c & d));
        }
        uint32_t q[8] = {a, b, c, d, e, f, g, v};
        for (int i = 0; i < 8; i++)
            h[i] += q[i];
    }

  public:
    void update(const void *p, size_t n) {
        const auto *b = static_cast<const uint8_t *>(p);
        length += n;
        while (n) {
            size_t take = n < 64 - used ? n : 64 - used;
            memcpy(block + used, b, take);
            used += take;
            b += take;
            n -= take;
            if (used == 64) {
                compress();
                used = 0;
            }
        }
    }
    std::string finish() {
        uint64_t bits = length * 8;
        uint8_t pad[128]{};
        pad[0] = 0x80;
        size_t n = used < 56 ? 56 - used : 120 - used;
        update(pad, n);
        uint8_t tail[8];
        for (int i = 0; i < 8; i++)
            tail[i] = uint8_t(bits >> (56 - i * 8));
        update(tail, 8);
        static const char hex[] = "0123456789abcdef";
        std::string out;
        for (uint32_t x : h)
            for (int shift = 28; shift >= 0; shift -= 4)
                out += hex[(x >> shift) & 15];
        return out;
    }
};
} // namespace lt

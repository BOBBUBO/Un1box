#define _GNU_SOURCE
#include "CHostFast.h"

#if defined(__linux__)
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <sched.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

// OpenSSL's EVP interface (1.1 and 3.x), declared here: the Swift SDK for Android ships libcrypto.a without headers.
typedef struct evp_cipher_ctx_st EVP_CIPHER_CTX;
typedef struct evp_cipher_st EVP_CIPHER;
extern EVP_CIPHER_CTX *EVP_CIPHER_CTX_new(void);
extern void EVP_CIPHER_CTX_free(EVP_CIPHER_CTX *);
extern int EVP_CIPHER_CTX_set_padding(EVP_CIPHER_CTX *, int);
extern int EVP_CipherInit_ex(EVP_CIPHER_CTX *, const EVP_CIPHER *, void *, const unsigned char *,
                             const unsigned char *, int);
extern int EVP_CipherUpdate(EVP_CIPHER_CTX *, unsigned char *, int *, const unsigned char *, int);
extern const EVP_CIPHER *EVP_aes_128_cbc(void);
extern const EVP_CIPHER *EVP_aes_192_cbc(void);
extern const EVP_CIPHER *EVP_aes_256_cbc(void);
typedef struct evp_md_st EVP_MD;
extern const EVP_MD *EVP_sha1(void);
extern unsigned char *HMAC(const EVP_MD *, const void *, int, const unsigned char *, size_t, unsigned char *,
                           unsigned int *);
typedef struct evp_md_ctx_st EVP_MD_CTX;
extern const EVP_MD *EVP_sha256(void);
extern EVP_MD_CTX *EVP_MD_CTX_new(void);
extern void EVP_MD_CTX_free(EVP_MD_CTX *);
extern int EVP_DigestInit_ex(EVP_MD_CTX *, const EVP_MD *, void *);
extern int EVP_DigestUpdate(EVP_MD_CTX *, const void *, size_t);
extern int EVP_DigestFinal_ex(EVP_MD_CTX *, unsigned char *, unsigned int *);

int lt_fast_aes_cbc(int decrypt, const void *key, size_t key_len, const void *iv, const void *in, size_t len,
                    void *out) {
    const EVP_CIPHER *cipher = key_len == 16 ? EVP_aes_128_cbc() : key_len == 24 ? EVP_aes_192_cbc()
                             : key_len == 32 ? EVP_aes_256_cbc() : NULL;
    if (!cipher || len % 16 || len > INT_MAX) return -1;
    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    if (!ctx) return -1;
    int n = 0;
    int ok = EVP_CipherInit_ex(ctx, cipher, NULL, key, iv, decrypt ? 0 : 1) == 1 &&
             EVP_CIPHER_CTX_set_padding(ctx, 0) == 1 &&
             EVP_CipherUpdate(ctx, out, &n, in, (int)len) == 1 && (size_t)n == len;
    EVP_CIPHER_CTX_free(ctx);
    return ok ? 0 : -1;
}

int lt_vfdecrypt_chunks(const void *aes_key, const void *hmac_key, size_t hmac_len, unsigned first,
                        size_t chunk_size, const void *in, size_t len, void *out) {
    if (!chunk_size || chunk_size % 16 || len % 16 || hmac_len > INT_MAX) return -1;
    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    if (!ctx) return -1;
    int ok = EVP_CipherInit_ex(ctx, EVP_aes_128_cbc(), NULL, aes_key, NULL, 0) == 1 &&
             EVP_CIPHER_CTX_set_padding(ctx, 0) == 1;
    for (size_t off = 0; ok && off < len; off += chunk_size, first++) {
        size_t n = len - off < chunk_size ? len - off : chunk_size;
        unsigned char be[4] = {first >> 24, first >> 16, first >> 8, first}, iv[20];
        unsigned int iv_len = 0;
        int got = 0;
        ok = HMAC(EVP_sha1(), hmac_key, (int)hmac_len, be, 4, iv, &iv_len) && iv_len == 20 &&
             EVP_CipherInit_ex(ctx, NULL, NULL, NULL, iv, 0) == 1 &&
             EVP_CipherUpdate(ctx, (unsigned char *)out + off, &got, (const unsigned char *)in + off, (int)n) == 1 &&
             (size_t)got == n;
    }
    EVP_CIPHER_CTX_free(ctx);
    return ok ? 0 : -1;
}

// A hole reads as zeros without a read: the digest is the file's own, but a sparse NAND page file (2.2 GB, a tenth
// of it written) costs its data reads plus hashing, not 2.2 GB of kernel zero-fill.
static const unsigned char zeros[1 << 20];

int lt_sha256_file(const char *path, unsigned char digest[32], void (*count)(void *, size_t), void *opaque) {
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    struct stat st;
    EVP_MD_CTX *ctx = EVP_MD_CTX_new();
    size_t cap = 4 << 20;
    unsigned char *buf = malloc(cap);
    int ok = ctx && buf && fstat(fd, &st) == 0 && EVP_DigestInit_ex(ctx, EVP_sha256(), NULL) == 1;
    off_t size = st.st_size, at = 0;
    while (ok && at < size) {
        off_t data = lseek(fd, at, SEEK_DATA);
        if (data < 0) data = errno == ENXIO ? size : at;  // ENXIO: a hole to the end; else no SEEK_DATA: read it all
        off_t end = data == at ? lseek(fd, at, SEEK_HOLE) : data;
        if (end < 0 || end > size) end = size;
        if (end <= at) end = size;
        if (data > at) {  // the hole [at, data)
            for (off_t left = data - at; ok && left > 0;) {
                size_t n = left < (off_t)sizeof zeros ? (size_t)left : sizeof zeros;
                ok = EVP_DigestUpdate(ctx, zeros, n) == 1;
                if (count) count(opaque, n);
                left -= n;
            }
        } else {  // data [at, end)
            for (off_t left = end - at, pos = at; ok && left > 0;) {
                ssize_t got = pread(fd, buf, left < (off_t)cap ? (size_t)left : cap, pos);
                if (got < 0 && errno == EINTR) continue;
                ok = got > 0 && EVP_DigestUpdate(ctx, buf, (size_t)got) == 1;
                if (ok && count) count(opaque, (size_t)got);
                if (got > 0) { left -= got; pos += got; }
            }
        }
        at = data > at ? data : end;
    }
    unsigned int len = 0;
    ok = ok && EVP_DigestFinal_ex(ctx, digest, &len) == 1 && len == 32;
    free(buf);
    if (ctx) EVP_MD_CTX_free(ctx);
    close(fd);
    return ok ? 0 : -1;
}

// A reflink where the filesystem has one (btrfs, XFS), else the data extents only: the copy keeps the source's holes
// (Foundation's copyItem writes them out, 17.7 GB for a 1.8 GB NAND).
int lt_sparse_copy(const char *from, const char *to) {
    int in = open(from, O_RDONLY | O_CLOEXEC);
    if (in < 0) return -1;
    struct stat st;
    int out = fstat(in, &st) == 0 ? open(to, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, st.st_mode & 0777) : -1;
    if (out < 0) { close(in); return -1; }
#ifndef FICLONE
#define FICLONE _IOW(0x94, 9, int)
#endif
    int ok = ioctl(out, FICLONE, in) == 0;
    if (!ok) {
        ok = ftruncate(out, st.st_size) == 0;
        size_t cap = 4 << 20;
        unsigned char *buf = ok ? malloc(cap) : NULL;
        ok = buf != NULL;
        for (off_t at = 0; ok && at < st.st_size;) {
            off_t data = lseek(in, at, SEEK_DATA);
            if (data < 0) { if (errno == ENXIO) break; data = at; }
            off_t end = lseek(in, data, SEEK_HOLE);
            if (end < 0 || end > st.st_size) end = st.st_size;
            for (off_t pos = data; ok && pos < end;) {
                ssize_t got = pread(in, buf, end - pos < (off_t)cap ? (size_t)(end - pos) : cap, pos);
                if (got < 0 && errno == EINTR) continue;
                ok = got > 0;
                for (ssize_t done = 0; ok && done < got;) {
                    ssize_t put = pwrite(out, buf + done, (size_t)(got - done), pos + done);
                    if (put < 0 && errno == EINTR) continue;
                    ok = put > 0;
                    if (ok) done += put;
                }
                if (ok) pos += got;
            }
            at = end;
        }
        free(buf);
    }
    ok = ok && fsync(out) == 0;
    close(in);
    if (close(out) != 0) ok = 0;
    if (!ok) unlink(to);
    return ok ? 0 : -1;
}

// As Portable's runtime (Runtime.cpp prefer_fast_cores) and lighttouch-oneshot do.
void lt_prefer_fast_cores(void) {
    const char *env = getenv("LT_ENGINE_CPUS");
    if (env && !strcmp(env, "off")) return;
    long capacity[CPU_SETSIZE], biggest = 0, smallest = LONG_MAX;
    int n = 0;
    for (; n < CPU_SETSIZE; n++) {
        char path[96];
        snprintf(path, sizeof path, "/sys/devices/system/cpu/cpu%d", n);
        if (access(path, F_OK) != 0) break;
        snprintf(path, sizeof path, "/sys/devices/system/cpu/cpu%d/cpu_capacity", n);
        FILE *f = fopen(path, "re");
        capacity[n] = 0;  // unknown (e.g. offline): never selected
        if (f) {
            if (fscanf(f, "%ld", &capacity[n]) != 1) capacity[n] = 0;
            fclose(f);
        }
        if (capacity[n] > biggest) biggest = capacity[n];
        if (capacity[n] > 0 && capacity[n] < smallest) smallest = capacity[n];
    }
    if (n == 0 || biggest <= 0 || smallest * 2 > biggest) return;
    cpu_set_t fast;
    CPU_ZERO(&fast);
    for (int cpu = 0; cpu < n; cpu++)
        if (capacity[cpu] * 2 >= biggest) CPU_SET(cpu, &fast);
    if (CPU_COUNT(&fast) > 0) sched_setaffinity(0, sizeof fast, &fast);
}

#else

int lt_fast_aes_cbc(int decrypt, const void *key, size_t key_len, const void *iv, const void *in, size_t len,
                    void *out) {
    (void)decrypt; (void)key; (void)key_len; (void)iv; (void)in; (void)len; (void)out;
    return -1;
}

int lt_vfdecrypt_chunks(const void *aes_key, const void *hmac_key, size_t hmac_len, unsigned first,
                        size_t chunk_size, const void *in, size_t len, void *out) {
    (void)aes_key; (void)hmac_key; (void)hmac_len; (void)first; (void)chunk_size; (void)in; (void)len; (void)out;
    return -1;
}

int lt_sha256_file(const char *path, unsigned char digest[32], void (*count)(void *, size_t), void *opaque) {
    (void)path; (void)digest; (void)count; (void)opaque;
    return -1;
}

int lt_sparse_copy(const char *from, const char *to) {
    (void)from; (void)to;
    return -1;
}

void lt_prefer_fast_cores(void) {}

#endif

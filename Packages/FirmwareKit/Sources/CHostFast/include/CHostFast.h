// Off the Mac, what the host does faster in C than FirmwareKit's portable Swift: AES-CBC and SHA-256 through the
// OpenSSL libcrypto that Foundation already links (CPU AES/SHA instructions, vs swift-crypto's block-at-a-time _CBC),
// sparse-file hashing and copying that skip holes, and keeping the process on the fast cores of a big.LITTLE phone.
#ifndef CHOSTFAST_H
#define CHOSTFAST_H
#include <stddef.h>

/// AES-CBC without padding: `len` a multiple of 16, `key_len` 16, 24 or 32. 0 on success, -1 otherwise
/// (including no libcrypto in this build: the caller then takes the portable path).
int lt_fast_aes_cbc(int decrypt, const void *key, size_t key_len, const void *iv, const void *in, size_t len,
                    void *out);

/// Decrypts whole encrcdsa (FileVault v2) chunks in place of VFDecrypt's per-chunk calls: chunk k of `in`
/// (`chunk_size` bytes, the last one possibly shorter but whole AES blocks) is image chunk `first + k`, its IV
/// HMAC-SHA1(hmac_key, be32(first + k))[:16]. 0 on success, -1 otherwise (or without libcrypto).
int lt_vfdecrypt_chunks(const void *aes_key, const void *hmac_key, size_t hmac_len, unsigned first,
                        size_t chunk_size, const void *in, size_t len, void *out);

/// SHA-256 of the file at `path` through libcrypto (the CPU's SHA instructions), reading only its data extents: a
/// hole is hashed as the zeros it reads as. `count` (may be NULL) gets each span's byte count, holes included.
/// 0 on success, -1 otherwise (or without libcrypto: the caller then hashes it the portable way).
int lt_sha256_file(const char *path, unsigned char digest[32], void (*count)(void *, size_t), void *opaque);

/// Copies the file `from` to the new file `to` keeping its holes: a reflink where the filesystem has one, else its
/// data extents only. 0 on success; -1 otherwise, with `to` removed.
int lt_sparse_copy(const char *from, const char *to);

/// Restricts this process (and the children it starts later) to the cores with at least half the biggest core's
/// capacity, when the slowest has under half (Android phones); a no-op elsewhere. LT_ENGINE_CPUS=off skips it.
void lt_prefer_fast_cores(void);

#endif

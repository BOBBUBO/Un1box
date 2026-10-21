// VFDecrypt: decrypt an Apple "encrcdsa" v2 disk image (an iOS rootfs DMG) with its known 36-byte key
// (AES-128 key + HMAC-SHA1 key); chunk n's IV = HMAC-SHA1(hmac_key, be32(n))[:16]. Ports imgtools/vfdecrypt.c.
//
//   try VFDecrypt.decrypt(input: encrypted, output: plain, key: key36)       // file to file
//   try VFDecrypt.decrypt(from: fd, output: plain, key: key36)               // any sequential fd (a pipe)
//   try ipsw.stream(osImage) { try VFDecrypt.decrypt(from: $0.fileDescriptor, output: plain, key: key36) }
//
// Streams in batches of whole chunks; the input is read once, front to back, so it can come straight off
// `unzip -p` without a temporary copy of the encrypted image.

import CHostFast
#if canImport(CommonCrypto)
import CommonCrypto
#endif
import Foundation
#if canImport(Android)
import Android
#endif

public enum VFDecrypt {
    public static func decrypt(input: URL, output: URL, key: Data) throws {
        let fd = open(input.path, O_RDONLY)
        guard fd >= 0 else { throw FirmwareError(.internal, "open \(input.path): \(String(cString: strerror(errno)))") }
        defer { close(fd) }
        try decrypt(from: fd, output: output, key: key)
    }

    public static func decrypt(from fd: Int32, output: URL, key: Data) throws {
        guard key.count == 36 else {
            throw FirmwareError(.keyMissing, "vfdecrypt key must be 36 bytes, got \(key.count)")
        }
        let aesKey = [UInt8](key.prefix(16))
        let hmacKey = [UInt8](key.dropFirst(16))
        var header = [UInt8](repeating: 0, count: 0x100)
        guard readFully(fd, &header, 0, 0x100) == 0x100, header[0..<8].elementsEqual("encrcdsa".utf8) else {
            throw FirmwareError(.unsupported, "not encrcdsa")
        }
        let be = { (o: Int, n: Int) in header[o..<o + n].reduce(UInt64(0)) { $0 << 8 | UInt64($1) } }
        let bs = Int(be(52, 4))
        let dataOffset = Int(be(64, 8))
        var left = be(56, 8)
        guard bs > 0, bs % 16 == 0, bs <= 1 << 24 else {
            throw FirmwareError(.unsupported, "encrcdsa block size \(bs)")
        }

        // Position at the data: header bytes already read count toward it; skip forward, never back.
        var ct = [UInt8](repeating: 0, count: bs)
        var pt = [UInt8](repeating: 0, count: bs)
        var carried = 0
        if dataOffset < 0x100 {
            carried = 0x100 - dataOffset
            ct.replaceSubrange(0..<carried, with: header[dataOffset..<0x100])
        } else {
            var skip = dataOffset - 0x100
            while skip > 0 {
                let n = readFully(fd, &ct, 0, min(skip, bs))
                guard n > 0 else { throw FirmwareError(.unsupported, "short image") }
                skip -= n
            }
        }

        guard FileManager.default.createFile(atPath: output.path, contents: nil),
            let out = FileHandle(forWritingAtPath: output.path)
        else {
            throw FirmwareError(.internal, "cannot create \(output.path)")
        }
        defer { try? out.close() }
        // Batches of whole chunks: one read and one write each, the chunks decrypted on every core (each chunk's IV
        // depends only on its index). Where libcrypto is missing (the Mac: CommonCrypto), chunk by chunk.
        let perBatch = max(1, (8 << 20) / bs)
        if dataOffset < 0x100 { ct.append(contentsOf: [UInt8](repeating: 0, count: perBatch * bs - bs)) } else {
            ct = [UInt8](repeating: 0, count: perBatch * bs)
        }
        pt = [UInt8](repeating: 0, count: perBatch * bs)
        let slices = max(1, min(ProcessInfo.processInfo.activeProcessorCount, 8))
        var n: UInt32 = 0
        while left > 0 {
            let r = carried + readFully(fd, &ct, carried, perBatch * bs - carried)
            carried = 0
            guard r > 0 else { throw FirmwareError(.unsupported, "short image") }
            guard r % 16 == 0 else {
                throw FirmwareError(.unsupported, "encrcdsa chunk \(n) is \(r % bs) bytes, not whole AES blocks")
            }
            let chunks = (r + bs - 1) / bs
            if !(try decryptBatch(ct, &pt, r, bs: bs, first: n, aesKey: aesKey, hmacKey: hmacKey, slices: slices)) {
                for k in 0..<chunks {
                    let at = k * bs, len = min(bs, r - at)
                    let i = n + UInt32(k)
                    var nb = [UInt8(i >> 24 & 0xFF), UInt8(i >> 16 & 0xFF), UInt8(i >> 8 & 0xFF), UInt8(i & 0xFF)]
                    var iv = [UInt8](repeating: 0, count: Int(CC_SHA1_DIGEST_LENGTH))
                    CCHmac(CCHmacAlgorithm(kCCHmacAlgSHA1), hmacKey, hmacKey.count, &nb, 4, &iv)
                    var got = 0
                    let status = ct.withUnsafeBytes { c in
                        pt.withUnsafeMutableBytes { p in
                            CCCrypt(CCOperation(kCCDecrypt), CCAlgorithm(kCCAlgorithmAES), 0, aesKey, 16, iv,
                                    c.baseAddress! + at, len, p.baseAddress! + at, len, &got)
                        }
                    }
                    guard status == kCCSuccess else { throw FirmwareError(.internal, "CCCrypt failed (\(status))") }
                }
            }
            let take = Int(min(UInt64(r), left))
            try pt.withUnsafeBytes { try out.write(contentsOf: Data(bytesNoCopy: UnsafeMutableRawPointer(mutating: $0.baseAddress!), count: take, deallocator: .none)) }
            left -= UInt64(take)
            n += UInt32(chunks)
        }
    }

    /// One batch through libcrypto (CHostFast), split across `slices` threads on chunk boundaries; false where this
    /// build has no libcrypto.
    static func decryptBatch(_ ct: [UInt8], _ pt: inout [UInt8], _ count: Int, bs: Int, first: UInt32,
                             aesKey: [UInt8], hmacKey: [UInt8], slices: Int) throws -> Bool {
        let chunks = (count + bs - 1) / bs
        let per = (chunks + slices - 1) / slices
        let failed = FailedSlice()
        ct.withUnsafeBytes { c in
            pt.withUnsafeMutableBytes { p in
                let (cb, pb) = (UInt(bitPattern: c.baseAddress!), UInt(bitPattern: p.baseAddress!))
                DispatchQueue.concurrentPerform(iterations: (chunks + per - 1) / per) { s in
                    let at = s * per * bs, len = min(per * bs, count - at)
                    let r = lt_vfdecrypt_chunks(aesKey, hmacKey, hmacKey.count, first + UInt32(s * per), bs,
                                                UnsafeRawPointer(bitPattern: cb + UInt(at)), len,
                                                UnsafeMutableRawPointer(bitPattern: pb + UInt(at)))
                    if r != 0 { failed.lock.withLock { failed.any = true } }
                }
            }
        }
        return !failed.any
    }

    /// Reads until `count` bytes or EOF; returns the bytes read.
    static func readFully(_ fd: Int32, _ buf: inout [UInt8], _ at: Int, _ count: Int) -> Int {
        var got = 0
        buf.withUnsafeMutableBytes { p in
            guard let start = p.baseAddress else { return }  // an empty buffer holds nothing
            while got < count {
                let r = read(fd, start + at + got, count - got)
                if r > 0 { got += r } else if r < 0, errno == EINTR { continue } else { break }
            }
        }
        return got
    }
}

/// Whether any of a batch's slices failed (decryptBatch's threads).
private final class FailedSlice: @unchecked Sendable {
    let lock = NSLock()
    var any = false
}

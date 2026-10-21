// The two CommonCrypto calls FirmwareKit makes (CCCrypt AES-CBC without padding, CCHmac SHA-1), over swift-crypto
// where Apple's CommonCrypto is missing (Linux, Android, Windows). AES-CBC goes to libcrypto first (CHostFast):
// swift-crypto's _CBC runs one block per BoringSSL call, ~50x slower on a 800 MB rootfs.
#if !canImport(CommonCrypto)
import CHostFast
import Crypto
import _CryptoExtras
import Foundation

typealias CCOperation = UInt32
typealias CCAlgorithm = UInt32
typealias CCHmacAlgorithm = UInt32
typealias CCCryptorStatus = Int32

let kCCEncrypt = 0
let kCCDecrypt = 1
let kCCAlgorithmAES = 0
let kCCHmacAlgSHA1 = 0
let kCCSuccess: CCCryptorStatus = 0
let CC_SHA1_DIGEST_LENGTH: Int32 = 20
private let kCCParamError: CCCryptorStatus = -4300
private let kCCBufferTooSmall: CCCryptorStatus = -4301

/// AES-CBC with no options (no padding): the only form FirmwareKit uses.
func CCCrypt(
    _ op: CCOperation, _ alg: CCAlgorithm, _ options: UInt32,
    _ key: UnsafeRawPointer?, _ keyLength: Int, _ iv: UnsafeRawPointer?,
    _ dataIn: UnsafeRawPointer?, _ dataInLength: Int,
    _ dataOut: UnsafeMutableRawPointer?, _ dataOutAvailable: Int, _ dataOutMoved: UnsafeMutablePointer<Int>?
) -> CCCryptorStatus {
    guard alg == CCAlgorithm(kCCAlgorithmAES), options == 0, let key, let iv, let dataOut,
        dataInLength % 16 == 0
    else { return kCCParamError }
    guard dataOutAvailable >= dataInLength else { return kCCBufferTooSmall }
    if dataInLength > 0,
        lt_fast_aes_cbc(op == CCOperation(kCCDecrypt) ? 1 : 0, key, keyLength, iv, dataIn, dataInLength, dataOut) == 0
    {
        dataOutMoved?.pointee = dataInLength
        return kCCSuccess
    }
    do {
        let k = SymmetricKey(data: UnsafeRawBufferPointer(start: key, count: keyLength))
        let v = try AES._CBC.IV(ivBytes: UnsafeRawBufferPointer(start: iv, count: 16))
        let input = dataInLength == 0 ? Data() : Data(bytes: dataIn!, count: dataInLength)
        let result =
            op == CCOperation(kCCDecrypt)
            ? try AES._CBC.decrypt(input, using: k, iv: v, noPadding: true)
            : try AES._CBC.encrypt(input, using: k, iv: v, noPadding: true)
        result.copyBytes(to: dataOut.assumingMemoryBound(to: UInt8.self), count: result.count)
        dataOutMoved?.pointee = result.count
        return kCCSuccess
    } catch {
        return kCCParamError
    }
}

/// HMAC-SHA1, the only one FirmwareKit uses.
func CCHmac(
    _ alg: CCHmacAlgorithm, _ key: UnsafeRawPointer, _ keyLength: Int,
    _ data: UnsafeRawPointer, _ dataLength: Int, _ macOut: UnsafeMutableRawPointer
) {
    let mac = HMAC<Insecure.SHA1>.authenticationCode(
        for: UnsafeRawBufferPointer(start: data, count: dataLength),
        using: SymmetricKey(data: UnsafeRawBufferPointer(start: key, count: keyLength))
    )
    mac.withUnsafeBytes { macOut.copyMemory(from: $0.baseAddress!, byteCount: $0.count) }
}
#endif

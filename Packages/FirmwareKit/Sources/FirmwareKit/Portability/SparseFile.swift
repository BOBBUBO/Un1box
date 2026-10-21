// A NAND page file is sparse: 2.2 GB, a tenth of it written. Off the Mac, CHostFast hashes it through libcrypto's
// SHA instructions reading only its data (a hole hashes as the zeros it reads as); FileHandle reads would move all
// 2.2 GB. nil where CHostFast cannot (the Mac, a failure): the caller takes the portable path.
import CHostFast
import Foundation

enum SparseFile {
    static func sha256(_ url: URL, count: (@Sendable (Int) -> Void)? = nil) -> String? {
        final class Counter {
            let count: (@Sendable (Int) -> Void)?
            init(_ count: (@Sendable (Int) -> Void)?) { self.count = count }
        }
        let counter = Counter(count)
        var digest = [UInt8](repeating: 0, count: 32)
        let status = withExtendedLifetime(counter) {
            lt_sha256_file(
                url.path, &digest,
                count == nil ? nil : { opaque, n in
                    Unmanaged<Counter>.fromOpaque(opaque!).takeUnretainedValue().count?(Int(n))
                },
                Unmanaged.passUnretained(counter).toOpaque())
        }
        return status == 0 ? digest.hexString : nil
    }
}

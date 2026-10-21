// A NAND page file is sparse: 2.2 GB, a tenth of it written. Off the Mac, CHostFast hashes it through libcrypto's
// SHA instructions reading only its data (a hole hashes as the zeros it reads as), and copies it keeping its holes;
// FileHandle reads and FileManager.copyItem would each move all 2.2 GB. nil / false where CHostFast cannot (the Mac,
// a failure): the caller takes the portable path.
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

    /// Copies `from` to the new file `to`, keeping holes; FileManager.copyItem where CHostFast cannot.
    static func copy(_ from: URL, to: URL) throws {
        if lt_sparse_copy(from.path, to.path) == 0 { return }
        try FileManager.default.copyItem(at: from, to: to)
    }

    /// `copy` over a directory tree (a NAND store: one level of page files).
    static func copyTree(_ from: URL, to: URL) throws {
        let fm = FileManager.default
        var isDir: ObjCBool = false
        guard fm.fileExists(atPath: from.path, isDirectory: &isDir), isDir.boolValue else {
            return try copy(from, to: to)
        }
        try fm.createDirectory(at: to, withIntermediateDirectories: false)
        for n in try fm.contentsOfDirectory(atPath: from.path) {
            try copyTree(from.appendingPathComponent(n), to: to.appendingPathComponent(n))
        }
    }
}

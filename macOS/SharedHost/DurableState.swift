import Darwin
import Foundation

/// Frontend recovery markers in the exclusively owned private state directory.
/// Every name is fixed by this owner; symlink markers are never followed.
nonisolated final class DurableState {
    private let descriptor: Int32
    enum Failure: Error { case invalidMarker, markerChanged }

    init(_ root: URL) throws {
        let fd = open(root.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard fd >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        descriptor = fd
    }
    deinit { Darwin.close(descriptor) }

    func read(_ name: String) throws -> Data? {
        let fd = openat(descriptor, name, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
        if fd < 0, errno == ENOENT { return nil }
        guard fd >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        defer { Darwin.close(fd) }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
            info.st_nlink == 1, info.st_size <= 8192 else { throw Failure.invalidMarker }
        var bytes = [UInt8](repeating: 0, count: 8193)
        var used = 0
        while used < bytes.count {
            let count = bytes.withUnsafeMutableBytes { buffer in
                Darwin.read(fd, buffer.baseAddress!.advanced(by: used), buffer.count - used)
            }
            if count < 0, errno == EINTR { continue }
            guard count >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
            if count == 0 { break }
            used += count
        }
        guard used <= 8192 else { throw Failure.invalidMarker }
        return Data(bytes.prefix(used))
    }

    func replace(_ name: String, with bytes: Data) throws {
        // Refuse a foreign link/special file even when recovery is explicit.
        _ = try read(name)
        let temporary = ".shared-marker-\(UUID().uuidString)"
        let fd = openat(descriptor, temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o600)
        guard fd >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        defer { Darwin.close(fd); unlinkat(descriptor, temporary, 0) }
        try bytes.withUnsafeBytes { buffer in
            var offset = 0
            while offset < buffer.count {
                let count = Darwin.write(fd, buffer.baseAddress!.advanced(by: offset), buffer.count - offset)
                if count < 0, errno == EINTR { continue }
                guard count > 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
                offset += count
            }
        }
        guard fsync(fd) == 0, renameat(descriptor, temporary, descriptor, name) == 0,
            fsync(descriptor) == 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
    }

    func remove(_ name: String, matching expected: Data) throws {
        guard try read(name) == expected else { throw Failure.markerChanged }
        guard unlinkat(descriptor, name, 0) == 0, fsync(descriptor) == 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
    }
}

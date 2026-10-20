import CryptoKit
import Darwin
import FirmwareKit
import Foundation
import HostRuntime
import LightTouchCore

/// Imports into an unpublished, unique private staging folder. Existing bases
/// and completed destinations are never rewritten or copied as a second base.
nonisolated func importArchive(_ args: HostArguments) throws {
    let fm = FileManager.default
    let source = args.url("source"), destination = args.url("destination")
    let expected = args.values["sha256"]!
    guard expected.count == 64, expected.allSatisfy({ "0123456789abcdef".contains($0) }) else {
        throw HostArguments.Failure.usage("Expected a lowercase archive SHA-256.")
    }
    var initial = stat()
    guard lstat(source.path, &initial) == 0, initial.st_mode & S_IFMT == S_IFREG,
        !fm.fileExists(atPath: destination.path),
        !StoragePathAuthority.canonicalPath(destination).hasPrefix(StoragePathAuthority.canonicalPath(source) + "/")
    else { throw HostArguments.Failure.usage("Choose a regular source and an absent destination.") }
    let fd = open(source.path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
    guard fd >= 0 else { throw POSIXError(.EIO) }
    let input = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
    defer { try? input.close() }
    var held = stat(); guard fstat(fd, &held) == 0, held.st_ino == initial.st_ino, held.st_dev == initial.st_dev else {
        throw HostArguments.Failure.usage("Source archive changed before inspection.")
    }
    var hasher = SHA256()
    while let block = try input.read(upToCount: (1024 * 1024)), !block.isEmpty {
        try Task.checkCancellation(); hasher.update(data: block)
    }
    guard hasher.finalize().map({ String(format: "%02x", $0) }).joined() == expected else {
        throw HostArguments.Failure.usage("Archive SHA-256 changed since launcher inspection.")
    }
    let staging = destination.deletingLastPathComponent().appendingPathComponent(".shared-import-\(UUID().uuidString)")
    guard !fm.fileExists(atPath: staging.path) else { throw POSIXError(.EEXIST) }
    var published = false
    defer {
        // Only this invocation's newly named unpublished staging is removable.
        if !published { try? DeviceStateStorage.removeTree(staging) }
    }
    try PreparedBase.unpack(source, into: staging) { fraction in
        try Task.checkCancellation()
        hostLine("import \(min(99, Int(fraction * 100)))%")
    }
    var after = stat()
    guard lstat(source.path, &after) == 0, after.st_ino == initial.st_ino, after.st_dev == initial.st_dev,
        after.st_size == initial.st_size,
        after.st_mtimespec.tv_sec == initial.st_mtimespec.tv_sec,
        after.st_mtimespec.tv_nsec == initial.st_mtimespec.tv_nsec,
        after.st_ctimespec.tv_sec == initial.st_ctimespec.tv_sec,
        after.st_ctimespec.tv_nsec == initial.st_ctimespec.tv_nsec,
        let lock = try DeviceLock.read(base: staging), let boardID = lock.board,
        ["n72ap", "n81ap"].contains(boardID), let board = Board(rawValue: boardID)
    else { throw HostArguments.Failure.usage("Source changed or unpacked base is unsupported.") }
    let required = try board.requiredFiles(strategy: lock.bootStrategy)
    for path in [required.boot, "nand", "identity.json"] + required.files {
        guard fm.fileExists(atPath: staging.appendingPathComponent(path).path) else {
            throw HostArguments.Failure.usage("Prepared base is incomplete: \(path)")
        }
    }
    try Task.checkCancellation()
    // Harden our newly extracted files before publication. An immutable flag
    // on the staging root would forbid the final atomic rename on macOS.
    if let walk = fm.enumerator(atPath: staging.path) {
        while let name = walk.nextObject() as? String {
            try Task.checkCancellation()
            let path = staging.appendingPathComponent(name)
            var attributes = stat()
            guard lstat(path.path, &attributes) == 0,
                [S_IFDIR, S_IFREG].contains(attributes.st_mode & S_IFMT),
                chmod(path.path, attributes.st_mode & ~mode_t(0o222)) == 0 else { throw POSIXError(.EIO) }
        }
    } else { throw POSIXError(.EIO) }
    guard chmod(staging.path, 0o555) == 0 else { throw POSIXError(.EIO) }
    guard !fm.fileExists(atPath: destination.path), renamex_np(staging.path, destination.path, UInt32(RENAME_EXCL)) == 0 else {
        throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
    }
    published = true
    hostLine("import 100%")
}

/// Finalize only the import worker's private, unpublished sibling stage.
/// Cleanup belongs to the caller; this operation never re-extracts or copies it.
nonisolated func finalizeImport(_ args: HostArguments) throws {
    let fm = FileManager.default
    let stage = args.url("stage"), destination = args.url("destination")
    let parent = stage.deletingLastPathComponent()
    guard stage.standardizedFileURL != destination.standardizedFileURL,
        parent.standardizedFileURL == destination.deletingLastPathComponent().standardizedFileURL
    else { throw HostArguments.Failure.usage("Stage and destination must be distinct siblings.") }
    for path in [parent, stage] {
        var info = stat()
        guard lstat(path.path, &info) == 0, info.st_mode & S_IFMT == S_IFDIR,
            info.st_uid == getuid(), info.st_mode & 0o077 == 0,
            StoragePathAuthority.canonicalPath(path) == path.standardizedFileURL.path
        else { throw HostArguments.Failure.usage("Import requires private owned directories without links.") }
    }
    var target = stat()
    guard lstat(destination.path, &target) != 0, errno == ENOENT else { throw POSIXError(.EEXIST) }
    try Task.checkCancellation()
    var reported = -1
    try PreparedBase.verifySharedImport(stage) { done, total in
        let percent = total > 0 ? Int(done * 100 / total) : 100
        if percent != reported {
            reported = percent
            hostLine("import phase=verifying percent=\(percent) done=\(done) total=\(total)")
        }
    }
    var protecting = false, published = false
    defer {
        // Frozen staged permissions must not prevent the caller's cancellation
        // cleanup. Completed destinations are never changed on this path.
        if protecting && !published {
            _ = chmod(stage.path, 0o700)
            if let walk = fm.enumerator(atPath: stage.path) {
                while let name = walk.nextObject() as? String {
                    let path = stage.appendingPathComponent(name)
                    var info = stat()
                    if lstat(path.path, &info) == 0, info.st_uid == getuid(),
                        [S_IFDIR, S_IFREG].contains(info.st_mode & S_IFMT) {
                        _ = chmod(path.path, info.st_mode | mode_t(0o200))
                    }
                }
            }
        }
    }
    protecting = true
    hostLine("import phase=protecting percent=0 done=0 total=1")
    guard let walk = fm.enumerator(atPath: stage.path) else { throw POSIXError(.EIO) }
    while let name = walk.nextObject() as? String {
        try Task.checkCancellation()
        let path = stage.appendingPathComponent(name)
        var info = stat()
        guard lstat(path.path, &info) == 0, info.st_uid == getuid(),
            [S_IFDIR, S_IFREG].contains(info.st_mode & S_IFMT),
            info.st_mode & S_IFMT != S_IFREG || info.st_nlink == 1,
            chmod(path.path, info.st_mode & ~mode_t(0o222)) == 0
        else { throw POSIXError(.EIO) }
    }
    guard chmod(stage.path, 0o500) == 0 else { throw POSIXError(.EIO) }
    hostLine("import phase=protecting percent=100 done=1 total=1")
    try Task.checkCancellation()
    guard renamex_np(stage.path, destination.path, UInt32(RENAME_EXCL)) == 0 else {
        throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
    }
    published = true
    hostLine("import phase=publishing percent=100 done=1 total=1")
}

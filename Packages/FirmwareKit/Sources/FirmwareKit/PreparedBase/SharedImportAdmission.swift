#if canImport(CryptoKit)
import CryptoKit
#else
import Crypto
#endif
#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#elseif canImport(Android)
import Android
#elseif canImport(ucrt)
import ucrt
#endif
import Foundation
import HostRuntime

extension PreparedBase {
    /// Final admission of an already extracted private stage. No extraction,
    /// conversion, identity reseeding or writable device state is performed.
    /// `progress` gets (bytes hashed, bytes to hash) on the calling thread; files hash on `workers` threads.
    nonisolated public static func verifySharedImport(
        _ base: URL, workers: Int = 4, progress: (Int64, Int64) -> Void = { _, _ in }
    ) throws {
        let fm = FileManager.default
        func asset(_ name: String, directory: Bool = false) throws -> URL {
            let parts = name.split(separator: "/", omittingEmptySubsequences: false)
            guard !name.contains("\\"), !parts.isEmpty,
                parts.allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." })
            else { throw invalid(base, "unsafe output path") }
            var path = base
            for (offset, part) in parts.enumerated() {
                path.appendPathComponent(String(part))
                var info = stat()
                let expected = offset < parts.count - 1 || directory ? S_IFDIR : S_IFREG
                guard lstat(path.path, &info) == 0, info.st_mode & S_IFMT == expected,
                    expected != S_IFREG || info.st_nlink == 1
                else { throw invalid(base, "output must be an independent regular file or directory: \(name)") }
            }
            return path
        }
        let lockURL = try asset("device.lock.json")
        guard (try fm.attributesOfItem(atPath: lockURL.path)[.size] as? NSNumber)?.intValue ?? Int.max <= 1024 * 1024 else {
            throw invalid(base, "device lock exceeds 1 MiB")
        }
        let lockBytes = try Data(contentsOf: lockURL)
        guard lockBytes.count <= 1024 * 1024,
            let lock = try JSONSerialization.jsonObject(with: lockBytes) as? [String: Any],
            lock["format"] as? Int == 1, let board = lock["board"] as? String,
            ["n72ap", "n81ap"].contains(board), let build = lock["build"] as? String, !build.isEmpty,
            let outputs = lock["outputs"] as? [String: [String: Any]], let nand = outputs["nand"]
        else { throw invalid(base, "unsupported prepared base identity") }
        func digest(_ record: [String: Any], _ key: String) throws -> String {
            guard let value = record[key] as? String, value.count == 64,
                value.allSatisfy({ "0123456789abcdefABCDEF".contains($0) })
            else { throw invalid(base, "missing authoritative SHA-256") }
            return value.lowercased()
        }
        func require(_ key: String, _ name: String, _ integrity: String = "sha256") throws {
            guard let record = outputs[key], record["path"] as? String == name else {
                throw invalid(base, "missing authoritative \(name) record")
            }
            _ = try digest(record, integrity)
        }
        // Every digest is queued here, then computed together (hashAll).
        var queued: [URL] = []
        func fileDigest(_ file: URL) -> Int { queued.append(file); return queued.count - 1 }
        let packed = nand["format"] as? String == "ITNAND01"
        try require("nand", "nand", packed ? "sha256" : "listing_sha256")
        try require("nor", "nor.bin")
        if board == "n72ap" {
            try require("gid_blobs", "gid-blobs.bin")
            if (lock["boot_strategy"] as? String ?? "iboot") == "iboot" { try require("iboot", "iBoot.bin") }
        } else { try require("kboot", "kboot.bin") }
        if packed { try PackedNAND.verify(base: base) }
        var checks: [(Int, String, String)] = []  // (queued file, expected digest, what failed)
        var listings: [(names: [String], files: [Int], digest: String, pages: Int?, name: String)] = []
        for (key, record) in outputs {
            try Task.checkCancellation()
            guard let name = record["path"] as? String else { throw invalid(base, "output path missing") }
            if record["sha256"] != nil {
                let file = try asset(name)
                // PackedNAND already hashed this file while checking its index.
                if !(packed && key == "nand") {
                    checks.append((fileDigest(file), try digest(record, "sha256"), "output checksum differs: \(name)"))
                }
            }
            if record["listing_sha256"] != nil {
                let directory = try asset(name, directory: true)
                guard let walk = fm.enumerator(atPath: directory.path) else { throw invalid(base, "unreadable NAND") }
                var names = [String]()
                while let relative = walk.nextObject() as? String {
                    try Task.checkCancellation()
                    var info = stat()
                    let file = directory.appendingPathComponent(relative)
                    guard lstat(file.path, &info) == 0 else { throw invalid(base, "NAND changed") }
                    if info.st_mode & S_IFMT == S_IFDIR { continue }
                    _ = try asset(name + "/" + relative)
                    names.append(relative)
                    guard names.count <= 1_000_000 else { throw invalid(base, "NAND listing exceeds supported bound") }
                }
                names.sort()
                listings.append((names, names.map { fileDigest(directory.appendingPathComponent($0)) },
                                 try digest(record, "listing_sha256"), record["pages"] == nil ? nil : (record["pages"] as? Int ?? -1), name))
            }
        }
        let identity = try asset("identity.json")
        if let record = lock["identity"] as? [String: Any], record["sha256"] != nil {
            checks.append((fileDigest(identity), try digest(record, "sha256"), "identity checksum differs"))
        }
        let digests = try hashAll(queued, base: base, workers: workers, progress: progress)
        for (file, expected, failure) in checks where digests[file] != expected { throw invalid(base, failure) }
        for listing in listings {
            var hash = SHA256()
            for (relative, file) in zip(listing.names, listing.files) {
                hash.update(data: Data((relative + " " + digests[file] + "\n").utf8))
            }
            guard hash.finalize().map({ String(format: "%02x", $0) }).joined() == listing.digest,
                listing.pages == nil || listing.pages == listing.names.count
            else { throw invalid(base, "\(listing.name) listing checksum or page count differs") }
        }
    }

    /// SHA-256 of each file, on up to `workers` threads. The calling thread stays in its task: it reports progress
    /// and turns a cancelled task into stopping the workers.
    nonisolated static func hashAll(_ files: [URL], base: URL, workers: Int, progress: (Int64, Int64) -> Void) throws
        -> [String]
    {
        var total: Int64 = 0
        for file in files {
            var info = stat()
            guard lstat(file.path, &info) == 0 else { throw invalid(base, "cannot open output") }
            total += Int64(info.st_size)
        }
        final class Shared: @unchecked Sendable {
            let lock = NSLock()
            var next = 0, done: Int64 = 0, stop = false, failure: String?
            var digests: [String]
            init(_ count: Int) { digests = Array(repeating: "", count: count) }
        }
        let shared = Shared(files.count)
        let group = DispatchGroup()
        for _ in 0..<max(1, min(workers, files.count)) {
            group.enter()
            Thread {
                defer { group.leave() }
                var buffer = [UInt8](repeating: 0, count: 1 << 20)
                while true {
                    shared.lock.lock()
                    let index = shared.next
                    shared.next += 1
                    let stop = shared.stop
                    shared.lock.unlock()
                    guard !stop, index < files.count else { return }
                    let fd = open(files[index].path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
                    var hash = SHA256(), failed = fd < 0
                    while !failed {
                        let n = buffer.withUnsafeMutableBytes { read(fd, $0.baseAddress, $0.count) }
                        if n < 0 && errno == EINTR { continue }
                        if n <= 0 { failed = n < 0; break }
                        buffer.withUnsafeBytes { hash.update(bufferPointer: UnsafeRawBufferPointer(rebasing: $0[..<n])) }
                        shared.lock.lock()
                        shared.done += Int64(n)
                        let stop = shared.stop
                        shared.lock.unlock()
                        if stop { break }
                    }
                    if fd >= 0 { close(fd) }
                    shared.lock.lock()
                    if failed {
                        shared.stop = true
                        shared.failure = shared.failure ?? "cannot read output \(files[index].lastPathComponent)"
                    } else {
                        shared.digests[index] = hash.finalize().map { String(format: "%02x", $0) }.joined()
                    }
                    shared.lock.unlock()
                }
            }.start()
        }
        var cancelled = false
        while group.wait(timeout: .now() + .milliseconds(100)) == .timedOut {
            shared.lock.lock()
            if Task.isCancelled { shared.stop = true; cancelled = true }
            let done = shared.done
            shared.lock.unlock()
            progress(done, total)
        }
        if cancelled || Task.isCancelled { throw CancellationError() }
        if let failure = shared.failure { throw invalid(base, failure) }
        progress(total, total)
        return shared.digests
    }
}

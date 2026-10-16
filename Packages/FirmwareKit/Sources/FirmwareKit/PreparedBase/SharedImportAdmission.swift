import CryptoKit
import Darwin
import Foundation
import HostRuntime

extension PreparedBase {
    /// Final admission of an already extracted private stage. No extraction,
    /// conversion, identity reseeding or writable device state is performed.
    nonisolated public static func verifySharedImport(_ base: URL) throws {
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
        func fileDigest(_ file: URL) throws -> String {
            let fd = open(file.path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
            guard fd >= 0 else { throw invalid(base, "cannot open output") }
            let input = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
            defer { try? input.close() }
            var hash = SHA256()
            while true {
                try Task.checkCancellation()
                guard let bytes = try input.read(upToCount: 1024 * 1024), !bytes.isEmpty else { break }
                hash.update(data: bytes)
            }
            return hash.finalize().map { String(format: "%02x", $0) }.joined()
        }
        let packed = nand["format"] as? String == "ITNAND01"
        try require("nand", "nand", packed ? "sha256" : "listing_sha256")
        try require("nor", "nor.bin")
        if board == "n72ap" {
            try require("gid_blobs", "gid-blobs.bin")
            if (lock["boot_strategy"] as? String ?? "iboot") == "iboot" { try require("iboot", "iBoot.bin") }
        } else { try require("kboot", "kboot.bin") }
        if packed { try PackedNAND.verify(base: base) }
        for (key, record) in outputs {
            try Task.checkCancellation()
            guard let name = record["path"] as? String else { throw invalid(base, "output path missing") }
            if record["sha256"] != nil {
                let file = try asset(name)
                // PackedNAND already hashed this file while checking its index.
                if !(packed && key == "nand") {
                    guard try fileDigest(file) == digest(record, "sha256") else {
                        throw invalid(base, "output checksum differs: \(name)")
                    }
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
                var hash = SHA256()
                for relative in names.sorted() {
                    try Task.checkCancellation()
                    let checksum = try fileDigest(directory.appendingPathComponent(relative))
                    hash.update(data: Data((relative + " " + checksum + "\n").utf8))
                }
                let checksum = hash.finalize().map { String(format: "%02x", $0) }.joined()
                guard try checksum == digest(record, "listing_sha256"),
                    record["pages"] == nil || record["pages"] as? Int == names.count
                else { throw invalid(base, "NAND listing checksum or page count differs") }
            }
        }
        let identity = try asset("identity.json")
        if let record = lock["identity"] as? [String: Any], record["sha256"] != nil {
            guard try fileDigest(identity) == digest(record, "sha256") else {
                throw invalid(base, "identity checksum differs")
            }
        }
    }
}

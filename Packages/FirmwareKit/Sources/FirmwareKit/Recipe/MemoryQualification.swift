import CryptoKit
import Foundation

/// Internal qualification surface, not part of the application/firmware CLI.
/// Calls the production implementation directly; no copied writer/digest loops.
/// This SPI keeps native assertions usable on CLT hosts lacking SwiftTesting.
@_spi(FirmwareMemoryQualification)
public enum FirmwareMemoryQualification {
    public struct Report: Codable, Sendable {
        public let status: String
        public let mode: String
        public let checks: [String]
        public let logicalInputBytes: Int?
        public let volumePages: Int?
        public let metadataPages: Int?
        public let files: Int?
        public let listingSHA256: String?
    }

    private enum ProbeError: Error { case assertion(String), expected }

    private static func require(_ value: Bool, _ label: String) throws {
        if !value { throw ProbeError.assertion(label) }
    }

    private static func expectError(_ label: String, _ body: () throws -> Void) throws {
        var didThrow = false
        do { try body() } catch { didThrow = true }
        try require(didThrow, label)
    }

    private static func create(_ url: URL, length: Int) throws -> FileHandle {
        try require(FileManager.default.createFile(atPath: url.path, contents: nil), "create sparse input")
        let file = try FileHandle(forWritingTo: url)
        do { try file.truncate(atOffset: UInt64(length)) } catch {
            try? file.close()
            throw error
        }
        return file
    }

    private static func metadata(blocks: Int) -> [String: Data] {
        Dictionary(uniqueKeysWithValues: N72NAND.metadataPages(blocks: blocks, epoch: 1).map {
            ("cs\($0.key.cs)/\($0.key.page).page", Data($0.value))
        })
    }

    /// Check every emitted byte, omitted zero path and input-order listing using
    /// independent whole-Data hashes, while calling the real streamed hash code.
    private static func verify(_ nand: URL, expected: [String: Data], names: [String]) throws -> String {
        let actual = try FileManager.default.subpathsOfDirectory(atPath: nand.path).filter { $0.hasSuffix(".page") }
        try require(Set(actual) == Set(expected.keys), "complete emitted/omitted page set")
        for name in names {
            try withFirmwareAutoreleasePool {
                let data = try Data(contentsOf: nand.appendingPathComponent(name))
                try require(data == expected[name], "exact page bytes: \(name)")
            }
        }
        let hashes = expected.mapValues { Preparer.sha256($0) }
        let listing = names.map { "\($0) \(hashes[$0]!)\n" }.joined()
        let result = try Preparer.nandListing(nand, files: names)
        try require(result.files == hashes, "per-page streamed SHA256")
        try require(result.sha256 == Preparer.sha256(Data(listing.utf8)), "caller-order full listing SHA256")
        return result.sha256
    }

    private static func ordinary(_ root: URL) throws -> Report {
        let volume = root.appendingPathComponent("short-sparse.img")
        let nand = root.appendingPathComponent("nand")
        let file = try create(volume, length: 3072 * 4096 + 17)
        do {
            let samples: [(block: Int, path: String, byte: UInt8, count: Int)] = [
                (0, "cs3/256.page", 0x11, 4096),
                (1023, "cs2/512.page", 0x22, 4096),
                (1024, "cs3/512.page", 0x33, 4096),
                (2048, "cs3/768.page", 0x44, 4096),
                (3072, "cs3/1024.page", 0x55, 17),
            ]
            var expected = metadata(blocks: 3073)
            for sample in samples {
                try file.seek(toOffset: UInt64(sample.block * 4096))
                let data = Data(repeating: sample.byte, count: sample.count)
                try file.write(contentsOf: data)
                expected[sample.path] = data + Data(count: 4096 - sample.count) + Data(N72NAND.blankSpare)
            }
            try file.close()
            let counts = try N72NAND.write(volume: volume, blocks: 3073, epoch: 1, out: nand)
            try require(counts.volume == 5 && counts.metadata == 50, "writer page counts")
            // Reversed input deliberately qualifies the caller-order contract.
            _ = try verify(nand, expected: expected, names: Array(expected.keys.sorted().reversed()))
        } catch {
            try? file.close()
            throw error
        }

        let digestFile = root.appendingPathComponent("digest-sparse.img")
        let length = 2 * (1 << 22) + 17
        let digestInput = try create(digestFile, length: length)
        do {
            try digestInput.seek(toOffset: UInt64((1 << 22) - 3))
            try digestInput.write(contentsOf: Data("across-boundary".utf8))
            try digestInput.seek(toOffset: UInt64(length - 1))
            try digestInput.write(contentsOf: Data([0xAB]))
            try digestInput.close()
        } catch {
            try? digestInput.close()
            throw error
        }
        var counted = 0
        let streamed = try Preparer.digest(digestFile, SHA256(), count: { counted += $0 })
        try require(streamed == Preparer.sha256(Data(contentsOf: digestFile)), "streamed digest equals whole sparse input")
        try require(counted == length, "digest callback counts every byte")

        try require(withFirmwareAutoreleasePool { "value".utf8.count } == 5, "pool return value")
        var expectedThrow = false
        do { try withFirmwareAutoreleasePool { throw ProbeError.expected } }
        catch ProbeError.expected { expectedThrow = true }
        try require(expectedThrow, "pool preserves original error")
        try expectError("missing-input digest throws") {
            _ = try Preparer.digest(root.appendingPathComponent("missing"), SHA256())
        }
        try expectError("missing-input writer throws") {
            _ = try N72NAND.write(volume: root.appendingPathComponent("missing"), blocks: 1, epoch: 1,
                                 out: root.appendingPathComponent("failed-nand"))
        }
        return Report(status: "PASS", mode: "ordinary", checks: [
            "sparse-writer-exact-pages-zero-omission-short-read-counts",
            "caller-ordered-concurrent-page-and-listing-hashes",
            "streamed-digest-whole-data-equivalence-and-callback-count",
            "pool-return-original-error-and-missing-input-errors",
        ], logicalInputBytes: nil, volumePages: nil, metadataPages: nil, files: nil, listingSHA256: nil)
    }

    private static func sparse(_ root: URL) throws -> Report {
        let blocks = 7168 * 1024 * 1024 / 4096
        let volume = root.appendingPathComponent("sparse-7g.img")
        let nand = root.appendingPathComponent("nand")
        let file = try create(volume, length: blocks * 4096)
        var expected = metadata(blocks: blocks)
        do {
            for block in stride(from: 0, to: blocks, by: 1024) {
                let data = Data(repeating: UInt8(block / 1024 % 255 + 1), count: 4096)
                try file.seek(toOffset: UInt64(block * 4096))
                try file.write(contentsOf: data)
                let page = N72NAND.predict(block)
                expected["cs\(page.cs)/\(page.page).page"] = data + Data(N72NAND.blankSpare)
            }
            try file.close()
        } catch {
            try? file.close()
            throw error
        }
        let counts = try N72NAND.write(volume: volume, blocks: blocks, epoch: 1, out: nand)
        try require(counts.volume == 1792 && counts.metadata == 50 && expected.count == 1842, "sparse frozen page counts")
        let listing = try verify(nand, expected: expected, names: expected.keys.sorted())
        // Independent Python oracle, checked against existing legacy metadata goldens.
        try require(listing == "d9911cab80831b8e8f40a1b5ce830352983294300e8de1b45a0c27a6945b070b", "sparse frozen full listing")
        return Report(status: "PASS", mode: "sparse", checks: [
            "seven-gib-logical-production-writer",
            "all-emitted-page-bytes-and-omitted-zero-paths",
            "production-concurrent-hashing-and-independent-frozen-listing",
        ], logicalInputBytes: blocks * 4096, volumePages: counts.volume,
        metadataPages: counts.metadata, files: expected.count, listingSHA256: listing)
    }

    /// The caller must provide a fresh private output path. Keep all generated
    /// non-firmware evidence; never alter or remove a supplied existing directory.
    public static func run(mode: String, out: URL) throws -> Report {
        try require(mode == "ordinary" || mode == "sparse", "known probe mode")
        try require(out.isFileURL && out.path.hasPrefix("/"), "absolute output path")
        try require(!FileManager.default.fileExists(atPath: out.path), "fresh output path required")
        try FileManager.default.createDirectory(at: out, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        if mode == "ordinary" { return try ordinary(out) }
        return try sparse(out)
    }
}

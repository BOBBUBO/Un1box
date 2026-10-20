#if canImport(CryptoKit)
import CryptoKit
#else
import Crypto
#endif
import Foundation
import Testing

@testable import FirmwareKit

/// The pool boundaries may change object lifetimes, never firmware bytes,
/// omitted zero pages, partial-read padding, counts, digests or thrown errors.
@Suite struct MemoryBoundedFirmwareTests {
    /// Opt-in external RSS experiment: scan the real 7 GiB logical extent while
    /// storing only one nonzero page per 4 MiB chunk (~7 MiB allocated input).
    /// Run in its own test process under /usr/bin/time -l for a useful peak RSS;
    /// this test proves bytes/counts, not a platform-independent memory threshold.
    @Test(.enabled(if: ProcessInfo.processInfo.environment["FK_TEST_MEMORY_STRESS"] == "1"))
    func sparseSevenGiBMemoryProbe() throws {
        try Oracle.withTemp { dir in
            let volume = dir.appendingPathComponent("sparse-7g.img")
            let out = dir.appendingPathComponent("nand")
            let blocks = 7168 * 1024 * 1024 / 4096
            #expect(FileManager.default.createFile(atPath: volume.path, contents: nil))
            let file = try FileHandle(forWritingTo: volume)
            try file.truncate(atOffset: UInt64(blocks * 4096))
            var expected = Dictionary(uniqueKeysWithValues: N72NAND.metadataPages(blocks: blocks, epoch: 1).map {
                ("cs\($0.key.cs)/\($0.key.page).page", Data($0.value))
            })
            var pages = 0
            for block in stride(from: 0, to: blocks, by: 1024) {
                let bytes = Data(repeating: UInt8(block / 1024 % 255 + 1), count: 4096)
                try file.seek(toOffset: UInt64(block * 4096))
                try file.write(contentsOf: bytes)
                let page = N72NAND.predict(block)
                expected["cs\(page.cs)/\(page.page).page"] = bytes + Data(N72NAND.blankSpare)
                pages += 1
            }
            try file.close()
            let counts = try N72NAND.write(volume: volume, blocks: blocks, epoch: 1, out: out)
            #expect(counts.volume == pages)
            let listing = try Preparer.nandListing(out, files: expected.keys.sorted())
            #expect(listing.files == expected.mapValues { Oracle.sha256($0) })
            let expectedListing = expected.keys.sorted().map { "\($0) \(Oracle.sha256(expected[$0]!))\n" }.joined()
            #expect(listing.sha256 == Oracle.sha256(Data(expectedListing.utf8)))
            print("memory-probe listing=\(listing.sha256) pages=\(pages) files=\(expected.count)")
        }
    }

    @Test func sparseWriterPreservesPagesAcrossChunkBoundariesAndShortFinalRead() throws {
        try Oracle.withTemp { dir in
            let volume = dir.appendingPathComponent("sparse.img")
            let out = dir.appendingPathComponent("nand")
            #expect(FileManager.default.createFile(atPath: volume.path, contents: nil))
            let file = try FileHandle(forWritingTo: volume)
            // Four chunks, mostly holes; final requested allocation block has
            // only 17 input bytes. The established writer pads it with zeros.
            try file.truncate(atOffset: UInt64(3072 * 4096 + 17))
            let samples: [(block: Int, path: String, byte: UInt8, bytes: Int)] = [
                (0, "cs3/256.page", 0x11, 4096),
                (1023, "cs2/512.page", 0x22, 4096),
                (1024, "cs3/512.page", 0x33, 4096),
                (2048, "cs3/768.page", 0x44, 4096),
                (3072, "cs3/1024.page", 0x55, 17),
            ]
            for sample in samples {
                try file.seek(toOffset: UInt64(sample.block * 4096))
                try file.write(contentsOf: Data(repeating: sample.byte, count: sample.bytes))
            }
            try file.close()
            var expected = Dictionary(uniqueKeysWithValues: N72NAND.metadataPages(blocks: 3073, epoch: 1).map {
                ("cs\($0.key.cs)/\($0.key.page).page", Data($0.value))
            })
            for sample in samples {
                expected[sample.path] = Data(repeating: sample.byte, count: sample.bytes)
                    + Data(count: 4096 - sample.bytes) + Data(N72NAND.blankSpare)
            }
            let counts = try N72NAND.write(volume: volume, blocks: 3073, epoch: 1, out: out)
            #expect(counts.volume == samples.count)
            #expect(counts.metadata == N72NAND.metadataPages(blocks: 3073, epoch: 1).count)
            let names = try FileManager.default.subpathsOfDirectory(atPath: out.path).filter { $0.hasSuffix(".page") }
            #expect(Set(names) == Set(expected.keys))
            for (path, bytes) in expected {
                #expect(try Data(contentsOf: out.appendingPathComponent(path)) == bytes)
            }
            // Caller-supplied ordering remains part of the listing contract.
            let sorted = names.sorted()
            let listing = try Preparer.nandListing(out, files: sorted)
            let independent = sorted.map { "\($0) \(Oracle.sha256(expected[$0]!))\n" }.joined()
            #expect(listing.sha256 == Oracle.sha256(Data(independent.utf8)))
            #expect(listing.files == expected.mapValues { Oracle.sha256($0) })
        }
    }

    @Test func streamedDigestMatchesWholeSparseFileAndReportsEveryByte() throws {
        try Oracle.withTemp { dir in
            let path = dir.appendingPathComponent("digest.img")
            #expect(FileManager.default.createFile(atPath: path.path, contents: nil))
            let file = try FileHandle(forWritingTo: path)
            let bytes = 2 * (1 << 22) + 17
            try file.truncate(atOffset: UInt64(bytes))
            try file.seek(toOffset: UInt64((1 << 22) - 3))
            try file.write(contentsOf: Data("across-boundary".utf8))
            try file.seek(toOffset: UInt64(bytes - 1))
            try file.write(contentsOf: Data([0xAB]))
            try file.close()
            var counted = 0
            let streamed = try Preparer.digest(path, SHA256(), count: { counted += $0 })
            let whole = try Data(contentsOf: path)
            #expect(streamed == Oracle.sha256(whole))
            #expect(counted == bytes)
        }
    }

    @Test func poolScopeReturnsValuesAndPreservesThrownErrors() throws {
        enum Failure: Error { case expected }
        #expect(withFirmwareAutoreleasePool { "value".utf8.count } == 5)
        #expect(throws: Failure.self) {
            try withFirmwareAutoreleasePool { throw Failure.expected }
        }
        try Oracle.withTemp { dir in
            #expect(throws: (any Error).self) {
                try Preparer.digest(dir.appendingPathComponent("missing"), SHA256())
            }
            #expect(throws: (any Error).self) {
                try N72NAND.write(
                    volume: dir.appendingPathComponent("missing"), blocks: 1, epoch: 1,
                    out: dir.appendingPathComponent("failed-nand")
                )
            }
        }
    }
}

import CryptoKit
import Foundation
import HostRuntime
import Testing

struct PackedNANDTests {
    @Test func packedIntegrityAndBounds() throws {
        let fm = FileManager.default
        let base = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try fm.createDirectory(at: base, withIntermediateDirectories: false)
        defer { try? fm.removeItem(at: base) }
        let nand = base.appendingPathComponent("nand")
        var valid = Data(repeating: 0, count: 24 + 8 * 1024 * 1024 + 2 * 4160)
        valid.replaceSubrange(0..<8, with: Data("ITNAND01".utf8))
        func word(_ bytes: inout Data, _ offset: Int, _ value: UInt32) {
            for i in 0..<4 { bytes[offset + i] = UInt8(truncatingIfNeeded: value >> (i * 8)) }
        }
        for (i, value) in [UInt32(4096), 64, 4, 524288].enumerated() { word(&valid, 8 + i * 4, value) }
        word(&valid, 24, 1); word(&valid, 24 + 8 * 1024 * 1024 - 4, 2)
        func write(_ data: Data, board: String = "n72ap", pages: Int = 2, digest: String? = nil) throws {
            try data.write(to: nand)
            let hash = digest ?? SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
            try JSONSerialization.data(withJSONObject: ["format": 1, "board": board, "outputs": ["nand": [
                "path": "nand", "format": "ITNAND01", "sha256": hash, "pages": pages,
                "source_listing_sha256": String(repeating: "a", count: 64),
            ]] as [String: Any]]).write(to: base.appendingPathComponent("device.lock.json"))
        }
        try write(valid)
        #expect(try PackedNAND.verify(base: base))
        try Data([1]).write(to: base.appendingPathComponent("kboot.bin"))
        let boot = try BootRecipe.preparedFiles(base: base, overlay: base.appendingPathComponent("overlay"), writableNOR: nil)
        #expect(boot.nand.path == nand.path && !boot.nand.hasDirectoryPath)
        var broken = valid
        word(&broken, 16, 8); try write(broken)
        #expect(throws: (any Error).self) { try PackedNAND.verify(base: base) }
        broken = valid; word(&broken, 24, 3); try write(broken)
        #expect(throws: (any Error).self) { try PackedNAND.verify(base: base) }
        broken = valid; word(&broken, 28, 1); try write(broken)
        #expect(throws: (any Error).self) { try PackedNAND.verify(base: base) }
        broken = valid; word(&broken, 24, 0); try write(broken)
        #expect(throws: (any Error).self) { try PackedNAND.verify(base: base) }
        try write(valid.dropLast())
        #expect(throws: (any Error).self) { try PackedNAND.verify(base: base) }
        try write(valid, board: "n81ap")
        #expect(throws: (any Error).self) { try PackedNAND.verify(base: base) }
        try write(valid, pages: -1)
        #expect(throws: (any Error).self) { try PackedNAND.verify(base: base) }
        try write(valid, digest: String(repeating: "0", count: 64))
        #expect(throws: (any Error).self) { try PackedNAND.verify(base: base) }
    }
}

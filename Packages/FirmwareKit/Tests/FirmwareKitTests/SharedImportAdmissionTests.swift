import CryptoKit
import Foundation
import Testing

@testable import FirmwareKit

struct SharedImportAdmissionTests {
    @Test func packedStageVerifiesAllAuthoritativeOutputs() throws {
        let fm = FileManager.default
        let base = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try fm.createDirectory(at: base, withIntermediateDirectories: false)
        defer { try? fm.removeItem(at: base) }
        var nand = Data(repeating: 0, count: 24 + 8 * 1024 * 1024)
        nand.replaceSubrange(0..<8, with: Data("ITNAND01".utf8))
        for (i, value) in [UInt32(4096), 64, 4, 524288].enumerated() {
            for byte in 0..<4 { nand[8 + i * 4 + byte] = UInt8(truncatingIfNeeded: value >> (byte * 8)) }
        }
        try nand.write(to: base.appendingPathComponent("nand"))
        func hash(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
        var outputs: [String: Any] = ["nand": ["path": "nand", "format": "ITNAND01", "pages": 0,
            "sha256": hash(nand), "source_listing_sha256": String(repeating: "a", count: 64)]]
        for (key, name) in [("nor", "nor.bin"), ("iboot", "iBoot.bin"), ("gid_blobs", "gid-blobs.bin")] {
            let data = Data(name.utf8)
            try data.write(to: base.appendingPathComponent(name))
            outputs[key] = ["path": name, "sha256": hash(data)]
        }
        let identity = Data("{}".utf8)
        try identity.write(to: base.appendingPathComponent("identity.json"))
        try JSONSerialization.data(withJSONObject: ["format": 1, "board": "n72ap", "build": "synthetic",
            "outputs": outputs, "identity": ["sha256": hash(identity)]])
            .write(to: base.appendingPathComponent("device.lock.json"))
        try PreparedBase.verifySharedImport(base)
        try Data("tampered".utf8).write(to: base.appendingPathComponent("nor.bin"))
        #expect(throws: (any Error).self) { try PreparedBase.verifySharedImport(base) }
        try Data("nor.bin".utf8).write(to: base.appendingPathComponent("nor.bin"))
        try Data("tampered".utf8).write(to: base.appendingPathComponent("identity.json"))
        #expect(throws: (any Error).self) { try PreparedBase.verifySharedImport(base) }
    }
}

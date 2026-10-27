#if canImport(CryptoKit)
import CryptoKit
#else
import Crypto
#endif
import Foundation
import Testing

@testable import FirmwareKit

/// HFSPlusEditor, the mount stand-in off the Mac: a volume formatted, edited through a host folder and read back
/// with HFSPlusVolume (the catalog reader the recipes use). FK_TEST_ROOTFS_DMG, a decrypted IPSW rootfs .dmg, adds
/// a round trip of a real system volume; FK_TEST_KEEP keeps the images for fsck_hfs on a Mac.
@Suite struct HFSPlusEditorTests {
    static func scratch(_ name: String) throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("hfsedit-\(name)-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    static func cleanup(_ dir: URL) {
        if ProcessInfo.processInfo.environment["FK_TEST_KEEP"] == nil { try? VolumeMount.removeTreeForTests(dir) }
    }

    @Test func formatEditAndReadBack() throws {
        let dir = try Self.scratch("format")
        defer { Self.cleanup(dir) }
        let image = dir.appendingPathComponent("data.img")
        try HFSPlusEditor.format(image, size: 64 << 20, name: "Data", journaled: true)
        let fresh = try HFSPlusVolume(image)
        #expect(fresh.signature == "HX" && fresh.blockSize == 4096)
        #expect(try fresh.journal()?.needsInit == true)
        #expect(try fresh.paths().map(\.path) == ["", ".journal", ".journal_info_block"])

        // a first edit: folders, files (one bigger than a block, one empty), a symlink
        let mnt = dir.appendingPathComponent("mnt")
        var editor = try HFSPlusEditor(image)
        try editor.extract(to: mnt)
        let big = Data((0..<20000).map { UInt8(truncatingIfNeeded: $0 &* 7) })
        try FileManager.default.createDirectory(at: mnt.appendingPathComponent("mobile/Library"), withIntermediateDirectories: true)
        try big.write(to: mnt.appendingPathComponent("mobile/Library/big.bin"))
        try Data().write(to: mnt.appendingPathComponent("empty"))
        try FileManager.default.createSymbolicLink(atPath: mnt.appendingPathComponent("link").path, withDestinationPath: "mobile/Library")
        chmod(mnt.appendingPathComponent("mobile").path, 0o750)
        try editor.commit(from: mnt)
        try VolumeMount.removeTreeForTests(mnt)

        let v = try HFSPlusVolume(image)
        let listing = Dictionary(uniqueKeysWithValues: try v.listing().map { ($0.path, $0) })
        #expect(
            Set(listing.keys)
                == ["", ".journal", ".journal_info_block", "empty", "link", "mobile", "mobile/Library", "mobile/Library/big.bin"]
        )
        #expect(listing["mobile/Library/big.bin"]?.sha256 == SHA256.hash(data: big).map { String(format: "%02x", $0) }.joined())
        #expect(listing["mobile/Library/big.bin"]?.size == 20000)
        #expect(listing["link"]?.link == "mobile/Library")
        #expect(listing["mobile"]?.mode == 0o40750)
        #expect(listing["empty"]?.uid == 501 && listing["empty"]?.gid == 20)
        #expect(v.fileCount == 5 && v.folderCount == 2)  // the journal's two files, big.bin, empty, link

        // a second edit: an untouched file keeps its blocks and ID even when moved; a rewritten one is new content
        let before = try v.record(at: "mobile/Library/big.bin")
        editor = try HFSPlusEditor(image)
        try editor.extract(to: mnt)
        try FileManager.default.moveItem(at: mnt.appendingPathComponent("mobile/Library/big.bin"), to: mnt.appendingPathComponent("moved.bin"))
        try Data("hello".utf8).write(to: mnt.appendingPathComponent("empty"))
        try FileManager.default.removeItem(at: mnt.appendingPathComponent("link"))
        try editor.commit(from: mnt)
        try VolumeMount.removeTreeForTests(mnt)
        let w = try HFSPlusVolume(image)
        let moved = try w.record(at: "moved.bin")
        #expect(moved.cnid == before.cnid && moved.data == before.data)
        #expect(try String(decoding: w.contents(w.record(at: "empty")), as: UTF8.self) == "hello")
        #expect((try? w.record(at: "link")) == nil)
        #expect(w.fileCount == 4)
    }

    /// TN1150's case-insensitive order: case folded, ignorables skipped, NUL after everything.
    @Test func caseFoldedCompare() {
        func cmp(_ a: String, _ b: String) -> Int { HFSPlusCaseFold.compare(Array(a.utf16), Array(b.utf16)) }
        #expect(cmp("abc", "ABC") == 0)
        #expect(cmp("a.txt", "B") < 0 && cmp("B", "c") < 0)
        #expect(cmp("ÆON", "æon") == 0)
        #expect(cmp("a\u{200C}b", "ab") == 0)  // zero-width non-joiner is ignorable
        #expect(cmp("a\u{0}", "az") > 0)  // NUL folds to 0xFFFF
        #expect(cmp("ab", "abc") < 0)
    }

    /// An H+ (case-insensitive) volume, as the keybag ramdisk is: the catalog is rebuilt in folded order, and two
    /// names one folder could not tell apart are refused rather than written.
    @Test func caseInsensitiveVolume() throws {
        let dir = try Self.scratch("hfsplus")
        defer { Self.cleanup(dir) }
        let image = dir.appendingPathComponent("v.img")
        try HFSPlusEditor.format(image, size: 16 << 20, name: "V", journaled: false)
        // format makes HX; relabel it H+ version 4 (an empty catalog is in either order)
        let fd = open(image.path, O_RDWR)
        #expect(fd >= 0)
        var plus: [UInt8] = [0x48, 0x2B, 0x00, 0x04]
        #expect(pwrite(fd, &plus, 4, 1024) == 4)
        close(fd)

        let mnt = dir.appendingPathComponent("mnt")
        var editor = try HFSPlusEditor(image)
        #expect(!editor.caseSensitive)
        try editor.extract(to: mnt)
        for name in ["c", "B", "a.txt"] { try Data(name.utf8).write(to: mnt.appendingPathComponent(name)) }
        try editor.commit(from: mnt)
        try VolumeMount.removeTreeForTests(mnt)
        let v = try HFSPlusVolume(image)
        // the catalog's own leaf order (paths() sorts by path)
        #expect(try v.catalog().filter { $0.parent == HFSPlusVolume.rootID }.map(\.name) == ["a.txt", "B", "c"])
        #expect(try String(decoding: v.contents(v.record(at: "B")), as: UTF8.self) == "B")

        editor = try HFSPlusEditor(image)
        try editor.extract(to: mnt)
        try Data().write(to: mnt.appendingPathComponent("b"))
        #expect(throws: FirmwareError.self) { try editor.commit(from: mnt) }
        try VolumeMount.removeTreeForTests(mnt)
    }

    @Test func growKeepsTheFilesAndFreesTheNewBlocks() throws {
        let dir = try Self.scratch("grow")
        defer { Self.cleanup(dir) }
        let image = dir.appendingPathComponent("v.img")
        try HFSPlusEditor.format(image, size: 32 << 20, name: "V", journaled: false)
        let mnt = dir.appendingPathComponent("mnt")
        let editor = try HFSPlusEditor(image)
        try editor.extract(to: mnt)
        try Data(repeating: 0xA5, count: 1 << 20).write(to: mnt.appendingPathComponent("a"))
        try editor.commit(from: mnt)
        try VolumeMount.removeTreeForTests(mnt)
        let small = try HFSPlusVolume(image)
        try HFSPlusEditor.grow(image, toBytes: 96 << 20)
        let v = try HFSPlusVolume(image)
        #expect(v.totalBlocks == (96 << 20) / 4096)
        #expect(v.freeBlocks == small.freeBlocks + v.totalBlocks - small.totalBlocks)
        #expect(try v.contents(v.record(at: "a")) == Data(repeating: 0xA5, count: 1 << 20))
        #expect(try FileManager.default.attributesOfItem(atPath: image.path)[.size] as? Int == 96 << 20)
    }

    /// A real system volume (the iPod touch 4G's 6.1.6 rootfs) through UDIFImage, then extracted and committed
    /// unchanged: every path's owner, mode, size and content as before, compression kept.
    @Test(.enabled(if: ProcessInfo.processInfo.environment["FK_TEST_ROOTFS_DMG"] != nil))
    func systemVolumeRoundTrip() async throws {
        let dmg = URL(fileURLWithPath: ProcessInfo.processInfo.environment["FK_TEST_ROOTFS_DMG"]!)
        let dir = try Self.scratch("rootfs")
        defer { Self.cleanup(dir) }
        let image = dir.appendingPathComponent("rootfs.hfs")
        var clock = ContinuousClock.now
        try await UDIF.extractRootfs(dmg: dmg, to: image)
        print("udif: \(ContinuousClock.now - clock)")
        if let want = ProcessInfo.processInfo.environment["FK_TEST_ROOTFS_SHA256"] {  // hdiutil's, for the same .dmg
            #expect(try Preparer.digest(image, SHA256()) == want, "UDIFImage's volume is not hdiutil's")
        }
        let before = try HFSPlusVolume(image).listing()
        clock = ContinuousClock.now
        try HFSPlusEditor.grow(image, toBytes: Int(try FileManager.default.attributesOfItem(atPath: image.path)[.size] as! UInt64) + (64 << 20))
        let mnt = dir.appendingPathComponent("mnt")
        let editor = try HFSPlusEditor(image)
        try editor.extract(to: mnt)
        print("extract: \(ContinuousClock.now - clock)")
        clock = ContinuousClock.now
        try Data("edited\n".utf8).write(to: mnt.appendingPathComponent("System/Library/CoreServices/edited.txt"))
        try editor.commit(from: mnt)
        print("commit: \(ContinuousClock.now - clock)")
        try VolumeMount.removeTreeForTests(mnt)
        let v = try HFSPlusVolume(image)
        let after = try v.listing().filter { $0.path != "System/Library/CoreServices/edited.txt" }
        let changed = zip(before, after).filter { $0 != $1 }.map(\.0.path)
        #expect(before.count == after.count && changed.isEmpty, "\(zip(before, after).filter { $0 != $1 }.prefix(3))")
        #expect(try v.paths().contains { $0.path == "System/Library/CoreServices/edited.txt" })
    }
}

extension VolumeMount {
    static func removeTreeForTests(_ url: URL) throws {
        #if os(macOS)
        let fm = FileManager.default
        var st = stat()
        guard lstat(url.path, &st) == 0 else { return }
        if st.st_mode & S_IFMT == S_IFDIR {
            chmod(url.path, 0o700)
            for name in try fm.contentsOfDirectory(atPath: url.path) { try removeTreeForTests(url.appendingPathComponent(name)) }
        }
        try fm.removeItem(at: url)
        #else
        try removeTree(url)
        #endif
    }
}

/// Developer hooks for checking images by hand: FK_DEV_HFS_GROW=<image>:<bytes> grows one in place;
/// FK_DEV_HFS_LIST=<image>:<out.json> writes its listing (owner, mode, flags, size, sha256, link per path);
/// FK_DEV_HFS_EXTRACT=<image>:<folder> extracts it.
@Suite struct HFSPlusEditorDevHooks {
    @Test(.enabled(if: ProcessInfo.processInfo.environment["FK_DEV_HFS_EXTRACT"] != nil))
    func extract() throws {
        let spec = ProcessInfo.processInfo.environment["FK_DEV_HFS_EXTRACT"]!.split(separator: ":")
        try HFSPlusEditor(URL(fileURLWithPath: String(spec[0]))).extract(to: URL(fileURLWithPath: String(spec[1])))
    }

    @Test(.enabled(if: ProcessInfo.processInfo.environment["FK_DEV_HFS_LIST"] != nil))
    func list() throws {
        let spec = ProcessInfo.processInfo.environment["FK_DEV_HFS_LIST"]!.split(separator: ":")
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(try HFSPlusVolume(URL(fileURLWithPath: String(spec[0]))).listing())
            .write(to: URL(fileURLWithPath: String(spec[1])))
    }

    @Test(.enabled(if: ProcessInfo.processInfo.environment["FK_DEV_HFS_GROW"] != nil))
    func grow() throws {
        let spec = ProcessInfo.processInfo.environment["FK_DEV_HFS_GROW"]!.split(separator: ":")
        try HFSPlusEditor.grow(URL(fileURLWithPath: String(spec[0])), toBytes: Int(spec[1])!)
    }
}

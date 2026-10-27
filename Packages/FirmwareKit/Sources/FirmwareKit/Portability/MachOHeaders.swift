import Foundation

/// The Mach-O header fields MachOSignature checks, read without MachOKit (which builds only on Apple platforms and
/// Linux): a thin or fat file's slices, each one's cpu and load command IDs.
struct MachOHeaders {
    struct Slice {
        var is64Bit: Bool
        var cputype: Int32, cpusubtype: Int32
        var commands: [UInt32]
    }

    static let main: UInt32 = 0x8000_0028, versionMinIphoneos: UInt32 = 0x25, dyldInfoOnly: UInt32 = 0x8000_0022
    static let buildVersion: UInt32 = 0x32, codeSignature: UInt32 = 0x1D

    var fat: Bool
    var slices: [Slice]

    init?(_ url: URL) {
        guard let data = try? Data(contentsOf: url, options: .alwaysMapped) else { return nil }
        self.init(data)
    }

    init?(_ data: Data) {
        let b = [UInt8](data.prefix(4))
        guard b.count == 4 else { return nil }
        if b == [0xCA, 0xFE, 0xBA, 0xBE] {
            func be(_ o: Int) -> Int { data.count >= o + 4 ? data[o..<o + 4].reduce(0) { $0 << 8 | Int($1) } : 0 }
            var slices: [Slice] = []
            for i in 0..<be(4) {
                let at = 8 + 20 * i, offset = be(at + 8), size = be(at + 12)
                guard offset + size <= data.count, let s = Self.slice(data.subdata(in: offset..<offset + size)) else {
                    return nil
                }
                slices.append(s)
            }
            fat = true
            self.slices = slices
        } else if let s = Self.slice(data) {
            fat = false
            slices = [s]
        } else {
            return nil
        }
    }

    /// A thin little-endian Mach-O: 0xFEEDFACE (32-bit, 28-byte header) or 0xFEEDFACF (64-bit, 32).
    static func slice(_ data: Data) -> Slice? {
        data.withUnsafeBytes { b -> Slice? in
            guard b.count >= 28 else { return nil }
            let magic = b.loadUnaligned(as: UInt32.self)
            guard magic == 0xFEED_FACE || magic == 0xFEED_FACF else { return nil }
            let is64 = magic == 0xFEED_FACF
            var s = Slice(
                is64Bit: is64,
                cputype: b.loadUnaligned(fromByteOffset: 4, as: Int32.self),
                cpusubtype: b.loadUnaligned(fromByteOffset: 8, as: Int32.self),
                commands: []
            )
            var at = is64 ? 32 : 28
            for _ in 0..<Int(b.loadUnaligned(fromByteOffset: 16, as: UInt32.self)) {
                guard at + 8 <= b.count else { return nil }
                s.commands.append(b.loadUnaligned(fromByteOffset: at, as: UInt32.self))
                let size = Int(b.loadUnaligned(fromByteOffset: at + 4, as: UInt32.self))
                guard size >= 8 else { return nil }
                at += size
            }
            return s
        }
    }
}

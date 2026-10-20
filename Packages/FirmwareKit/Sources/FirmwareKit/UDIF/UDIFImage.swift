// UDIFImage: a UDIF disk image (an IPSW's rootfs or ramdisk .dmg) read without hdiutil, for the hosts that have
// none (Linux, Android, Windows). The koly trailer locates the plist; each blkx entry's mish table maps runs of
// sectors to raw, zero-fill, zlib or ADC chunks of the data fork.
//
//   let image = try UDIFImage(dmg)
//   let head = try image.read(sector: 0, count: 64)                 // the partition map
//   try image.write(to: fd, sectors: start..<start + count)          // a partition, at offset 0 of fd

import Foundation
import zlib

public struct UDIFImage: Sendable {
    struct Chunk: Sendable {
        enum Kind: Sendable { case zero, raw, zlib, adc }
        let kind: Kind
        let sector: Int  // first output sector
        let sectors: Int
        let offset: Int  // in the file
        let length: Int
    }

    public let url: URL
    public let sectorCount: Int
    let chunks: [Chunk]  // by output sector

    public init(_ url: URL) throws {
        self.url = url
        let f = try FileHandle(forReadingFrom: url)
        defer { try? f.close() }
        let end = try f.seekToEnd()
        guard end >= 512 else { throw FirmwareError(.unsupported, "\(url.lastPathComponent): not a UDIF image") }
        try f.seek(toOffset: end - 512)
        let koly = [UInt8](try f.read(upToCount: 512) ?? Data())
        guard koly.count == 512, koly.starts(with: Array("koly".utf8)) else {
            throw FirmwareError(.unsupported, "\(url.lastPathComponent): no koly trailer (not a UDIF image)")
        }
        let dataFork = Int(be64(koly, 0x18))
        let xmlOffset = be64(koly, 0xD8), xmlLength = Int(be64(koly, 0xE0))
        sectorCount = Int(be64(koly, 0x1EC))
        try f.seek(toOffset: xmlOffset)
        guard let xml = try f.read(upToCount: xmlLength), xml.count == xmlLength,
            let plist = try PropertyListSerialization.propertyList(from: xml, format: nil) as? [String: Any],
            let blkx = (plist["resource-fork"] as? [String: Any])?["blkx"] as? [[String: Any]]
        else { throw FirmwareError(.unsupported, "\(url.lastPathComponent): no blkx table") }
        var chunks: [Chunk] = []
        for entry in blkx {
            guard let data = entry["Data"] as? Data else { continue }
            let mish = [UInt8](data)
            guard mish.count >= 204, mish.starts(with: Array("mish".utf8)) else {
                throw FirmwareError(.unsupported, "\(url.lastPathComponent): bad blkx entry")
            }
            let first = Int(be64(mish, 8)), base = Int(be64(mish, 24)) + dataFork
            let n = Int(be32(mish, 200))
            guard mish.count >= 204 + 40 * n else {
                throw FirmwareError(.unsupported, "\(url.lastPathComponent): truncated blkx entry")
            }
            for i in 0..<n {
                let c = 204 + 40 * i
                let type = be32(mish, c)
                let kind: Chunk.Kind
                switch type {
                case 0x0000_0000, 0x0000_0002: kind = .zero
                case 0x0000_0001: kind = .raw
                case 0x8000_0004: kind = .adc
                case 0x8000_0005: kind = .zlib
                case 0x7FFF_FFFE, 0xFFFF_FFFF: continue  // comment, terminator
                default:
                    throw FirmwareError(
                        .unsupported,
                        "\(url.lastPathComponent): chunk type 0x\(String(type, radix: 16)) (bzip2/LZFSE/LZMA) is not supported here"
                    )
                }
                let sectors = Int(be64(mish, c + 16))
                if sectors == 0 { continue }
                chunks.append(
                    Chunk(
                        kind: kind,
                        sector: first + Int(be64(mish, c + 8)),
                        sectors: sectors,
                        offset: base + Int(be64(mish, c + 24)),
                        length: Int(be64(mish, c + 32))
                    )
                )
            }
        }
        self.chunks = chunks.sorted { $0.sector < $1.sector }
    }

    /// Sectors `sector..<sector + count` (zeros where no chunk covers them).
    public func read(sector: Int, count: Int) throws -> Data {
        let fd = open(url.path, O_RDONLY)
        guard fd >= 0 else { throw FirmwareError(.internal, "cannot open \(url.path)") }
        defer { close(fd) }
        var out = Data(count: count * 512)
        for c in chunks where c.sector < sector + count && c.sector + c.sectors > sector {
            let bytes = try decode(c, fd)
            let from = max(c.sector, sector), to = min(c.sector + c.sectors, sector + count)
            out.replaceSubrange(
                (from - sector) * 512..<(to - sector) * 512,
                with: bytes[(from - c.sector) * 512..<(to - c.sector) * 512]
            )
        }
        return out
    }

    /// Writes sectors `range` to `fd` from its offset 0, on `workers` threads; zero runs are left as holes (the
    /// caller sizes the file). `progress` gets the bytes done so far.
    public func write(
        to out: Int32,
        sectors range: Range<Int>,
        workers: Int = 4,
        progress: (@Sendable (Int) -> Void)? = nil
    ) throws {
        let fd = open(url.path, O_RDONLY)
        guard fd >= 0 else { throw FirmwareError(.internal, "cannot open \(url.path)") }
        defer { close(fd) }
        let work = chunks.filter { $0.kind != .zero && $0.sector < range.upperBound && $0.sector + $0.sectors > range.lowerBound }
        let state = WriteState()
        DispatchQueue.concurrentPerform(iterations: max(1, workers)) { worker in
            var i = worker
            while i < work.count, !state.failed {
                let c = work[i]
                do {
                    let bytes = try decode(c, fd)
                    let from = max(c.sector, range.lowerBound), to = min(c.sector + c.sectors, range.upperBound)
                    try bytes.withUnsafeBytes { b in
                        let slice = UnsafeRawBufferPointer(rebasing: b[(from - c.sector) * 512..<(to - c.sector) * 512])
                        guard slice.contains(where: { $0 != 0 }) else { return }
                        var done = 0
                        while done < slice.count {
                            let n = pwrite(
                                out, slice.baseAddress! + done, slice.count - done,
                                off_t((from - range.lowerBound) * 512 + done))
                            guard n > 0 else { throw FirmwareError(.internal, "write: \(String(cString: strerror(errno)))") }
                            done += n
                        }
                    }
                    progress?(state.add((to - from) * 512))
                } catch {
                    state.fail(error)
                }
                i += max(1, workers)
            }
        }
        if let error = state.error { throw error }
    }

    func decode(_ c: Chunk, _ fd: Int32) throws -> [UInt8] {
        let size = c.sectors * 512
        if c.kind == .zero { return [UInt8](repeating: 0, count: size) }
        var input = [UInt8](repeating: 0, count: c.length)
        var got = 0
        while got < c.length {
            let n = input.withUnsafeMutableBytes { pread(fd, $0.baseAddress! + got, c.length - got, off_t(c.offset + got)) }
            guard n > 0 else { throw FirmwareError(.unsupported, "\(url.lastPathComponent): ends inside a chunk") }
            got += n
        }
        switch c.kind {
        case .raw: return input.count >= size ? Array(input.prefix(size)) : input + [UInt8](repeating: 0, count: size - input.count)
        case .zlib:
            var out = [UInt8](repeating: 0, count: size)
            var length = uLongf(size)
            guard uncompress(&out, &length, input, uLong(input.count)) == Z_OK else {
                throw FirmwareError(.unsupported, "\(url.lastPathComponent): bad zlib chunk at sector \(c.sector)")
            }
            return out
        case .adc: return try Self.adc(input, size)
        case .zero: return []
        }
    }

    /// Apple Data Compression: runs of literals, and back-references 3-18 bytes long (2-byte form) or 4-67 long
    /// (3-byte form, 16-bit distance).
    static func adc(_ input: [UInt8], _ size: Int) throws -> [UInt8] {
        var out = [UInt8]()
        out.reserveCapacity(size)
        var i = 0
        while i < input.count, out.count < size {
            let b = Int(input[i])
            if b & 0x80 != 0 {
                let n = (b & 0x7F) + 1
                guard i + 1 + n <= input.count else { throw FirmwareError(.unsupported, "bad ADC literal run") }
                out += input[i + 1..<i + 1 + n]
                i += 1 + n
                continue
            }
            let length: Int, distance: Int
            if b & 0x40 != 0 {
                guard i + 2 < input.count else { throw FirmwareError(.unsupported, "bad ADC match") }
                length = (b & 0x3F) + 4
                distance = Int(input[i + 1]) << 8 | Int(input[i + 2])
                i += 3
            } else {
                guard i + 1 < input.count else { throw FirmwareError(.unsupported, "bad ADC match") }
                length = (b >> 2 & 0x0F) + 3
                distance = (b & 0x03) << 8 | Int(input[i + 1])
                i += 2
            }
            let from = out.count - distance - 1
            guard from >= 0 else { throw FirmwareError(.unsupported, "bad ADC distance") }
            for k in 0..<length { out.append(out[from + k]) }
        }
        if out.count < size { out += [UInt8](repeating: 0, count: size - out.count) }
        return Array(out.prefix(size))
    }
}

private final class WriteState: @unchecked Sendable {
    private let lock = NSLock()
    private var done = 0
    private(set) var error: Error?
    var failed: Bool { lock.withLock { error != nil } }
    func add(_ n: Int) -> Int { lock.withLock { done += n; return done } }
    func fail(_ e: Error) { lock.withLock { if error == nil { error = e } } }
}

private func be64(_ b: [UInt8], _ o: Int) -> UInt64 { UInt64(be32(b, o)) << 32 | UInt64(be32(b, o + 4)) }

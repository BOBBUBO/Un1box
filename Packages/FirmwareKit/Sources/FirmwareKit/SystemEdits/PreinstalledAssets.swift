// PreinstalledAssets: 6.x's first-boot asset preinstall, done on the /private/var skeleton instead.
//
// A 6.x restore leaves /var/mobile/Library/PreinstalledAssets/<type>.cpio (odc cpio: one <hash>.cpio per asset and
// the type's catalog, <type>.plist). On the first boot mobileassetd copies each asset whose
// __PreinstallRegionCodes (if any) hold the unit's region to Assets/<type>/<hash>.asset/<hash>, writes the type's
// catalog of those (each "__DownloadState" "Verifying", without FormatVersion), removes PreinstalledAssets and
// writes Assets/preinstall-is-complete.plist; DataMigrator's later migrators wait for it. On n81ap 6.1.6 that is
// 142 MB of dictionaries through the emulated flash, ~45 s of the seal boot. The layout is what mobileassetd left
// on a sealed n81ap 10B500 data volume; the fonts are China, Taiwan and Hong Kong units' only.

import Foundation

extension SystemEdits {
    static let preinstalledAssets = "mobile/Library/PreinstalledAssets"
    static let assets = "mobile/Library/Assets"

    /// mobileassetd's preinstall, for 6.x (`version` and `build` as SystemVersion.plist has them; `region` the unit's
    /// region code, "LL" of "LL/A"). Returns the assets installed, or nil when the skeleton has nothing to preinstall.
    static func seedPreinstalledAssets(
        _ skeleton: URL,
        major: Int,
        version: String,
        build: String,
        region: String
    ) throws -> [String]? {
        let fm = FileManager.default
        let source = skeleton.appendingPathComponent(preinstalledAssets)
        guard major == 6, let names = try? fm.contentsOfDirectory(atPath: source.path) else { return nil }
        let archives = names.filter { $0.hasSuffix(".cpio") }.sorted()
        guard !archives.isEmpty else { return nil }
        let root = skeleton.appendingPathComponent(assets)
        var installed: [String] = []
        for archive in archives {
            let type = String(archive.dropLast(".cpio".count))
            let url = source.appendingPathComponent(archive)
            guard
                let catalog = try PropertyListSerialization.propertyList(
                    from: try ODCArchive.read(url, member: type + ".plist"),
                    format: nil
                ) as? [String: Any],
                let all = catalog["Assets"] as? [[String: Any]]
            else { throw FirmwareError(.unsupported, "\(archive): no \(type).plist catalog") }
            let wanted = all.filter { ($0["__PreinstallRegionCodes"] as? [String])?.contains(region) ?? true }
            guard !wanted.isEmpty else { continue }
            let dir = root.appendingPathComponent(type)
            var members: [String: URL] = [:]
            var entries: [[String: Any]] = []
            for var asset in wanted {
                guard let rel = asset["__RelativePath"] as? String, rel.hasSuffix(".cpio") else {
                    throw FirmwareError(.unsupported, "\(archive): an asset without a .cpio __RelativePath")
                }
                let hash = String((rel.split(separator: "/").last ?? "").dropLast(".cpio".count))
                let home = dir.appendingPathComponent(hash + ".asset")
                try mkdirs(home)
                members[hash + ".cpio"] = home.appendingPathComponent(hash)
                asset["__DownloadState"] = "Verifying"
                entries.append(asset)
                installed.append(type + "/" + hash)
            }
            try ODCArchive.extract(url, members)
            var out = catalog
            out["Assets"] = entries
            out["FormatVersion"] = nil
            try put(
                PropertyListSerialization.data(fromPropertyList: out, format: .xml, options: 0),
                dir.appendingPathComponent(type + ".plist")
            )
        }
        try fm.removeItem(at: source)
        try mkdirs(root)
        try put(
            PropertyListSerialization.data(
                fromPropertyList: ["BuildVersion": build, "ProductVersion": version],
                format: .xml,
                options: 0
            ),
            root.appendingPathComponent("preinstall-is-complete.plist")
        )
        return installed
    }
}

/// The odc ("070707") cpio archives 6.x ships its preinstalled assets in: a 76-byte octal header, the name (NUL
/// included), the data, no padding, up to TRAILER!!!.
enum ODCArchive {
    /// Walks `url`'s members: `body` gets each member's name ("./" dropped), size and handle, positioned at its data;
    /// whatever it leaves unread is skipped.
    static func walk(_ url: URL, _ body: (String, Int, FileHandle) throws -> Void) throws {
        let fh = try FileHandle(forReadingFrom: url)
        defer { try? fh.close() }
        var offset: UInt64 = 0
        while true {
            try fh.seek(toOffset: offset)
            let header = try fh.read(upToCount: 76) ?? Data()
            guard header.count == 76, header.prefix(6) == Data("070707".utf8) else {
                throw FirmwareError(.unsupported, "\(url.lastPathComponent): not an odc cpio at \(offset)")
            }
            func octal(_ at: Int, _ width: Int) throws -> Int {
                let digits = String(decoding: header[at..<at + width], as: UTF8.self)
                guard let v = Int(digits, radix: 8) else {
                    throw FirmwareError(.unsupported, "\(url.lastPathComponent): bad cpio header at \(offset)")
                }
                return v
            }
            let nameSize = try octal(59, 6), size = try octal(65, 11)
            let raw = try fh.read(upToCount: nameSize) ?? Data()
            var name = String(decoding: raw.prefix { $0 != 0 }, as: UTF8.self)
            if name == "TRAILER!!!" { return }
            if name.hasPrefix("./") { name.removeFirst(2) }
            try body(name, size, fh)
            offset += UInt64(76 + nameSize + size)
        }
    }

    /// One member's data.
    static func read(_ url: URL, member: String) throws -> Data {
        var found: Data?
        try walk(url) { name, size, fh in
            if name == member { found = try fh.read(upToCount: size) ?? Data() }
        }
        guard let found else { throw FirmwareError(.unsupported, "\(url.lastPathComponent): no \(member)") }
        return found
    }

    /// The named members, each to its file, in 1 MiB pieces.
    static func extract(_ url: URL, _ targets: [String: URL]) throws {
        var left = targets
        try walk(url) { name, size, fh in
            guard let target = left.removeValue(forKey: name) else { return }
            guard FileManager.default.createFile(atPath: target.path, contents: nil) else {
                throw FirmwareError(.internal, "cannot create \(target.path)")
            }
            let out = try FileHandle(forWritingTo: target)
            defer { try? out.close() }
            var remaining = size
            while remaining > 0 {
                let piece = try fh.read(upToCount: min(remaining, 1 << 20)) ?? Data()
                guard !piece.isEmpty else { throw FirmwareError(.unsupported, "\(url.lastPathComponent): \(name) cut short") }
                try out.write(contentsOf: piece)
                remaining -= piece.count
            }
        }
        guard left.isEmpty else {
            throw FirmwareError(.unsupported, "\(url.lastPathComponent): no \(left.keys.sorted().joined(separator: ", "))")
        }
    }
}

// HFSPlusEditor: file-level edits of an HFS+ volume image without a mount, for the hosts whose kernels have no
// HFS+ driver an ordinary user may use (Linux, Android, Windows). VolumeMount runs a mount's body against it:
// `extract` lays the volume's files out in a host folder (modes and dates as the catalog has them), the body
// edits them there as it would a mount, and `commit` writes back what changed.
//
//   let editor = try HFSPlusEditor(image)
//   try editor.extract(to: folder)                                   // every visible file and folder
//   ...                                                              // edit `folder`
//   try editor.commit(from: folder)                                  // the volume now holds the folder's tree
//   try HFSPlusEditor.grow(image, toBytes: n)                        // a bigger volume, the file grown with it
//   try HFSPlusEditor.format(image, size: n, name: "Data", journaled: true)
//
// What commit keeps, as an edit through macOS's driver would: an untouched file or folder its catalog record (ID,
// owner, flags, dates), blocks and extended attributes (HFS compression included), wherever the edit moved it; a
// rewritten file its record, owner and attributes but not its compression (stored plain, as the driver stores a
// compressed file once written to); a new item the next catalog ID and the host user's owner, 501:20, as a
// noowners mount records it (callers set owners with HFSPlusVolume.setOwner afterwards). The catalog, attributes
// and extents overflow B-trees are written afresh from their records into their files' blocks (grown when they no
// longer fit); blocks that come free are zeroed. The hidden items at the root (the journal files, the hard-link
// folders) are kept as they are and not extracted. Hard links are not supported (IPSW volumes have none).

import Foundation
#if canImport(Android)
import Android
#endif

final class HFSPlusEditor: @unchecked Sendable {
    struct Extent: Equatable {
        var start: Int
        var count: Int
    }

    /// A fork: its logical size, clump size and every extent (inline and overflow), in file order.
    struct Fork {
        var logicalSize: UInt64 = 0
        var clumpSize: UInt32 = 0
        var extents: [Extent] = []
        /// The record's totalBlocks, which the extents reach once the overflow ones are added.
        var recordedBlocks = 0
        var totalBlocks: Int { extents.reduce(0) { $0 + $1.count } }

        init() {}
        init(_ b: [UInt8], _ o: Int) {
            logicalSize = be64(b, o)
            clumpSize = be32(b, o + 8)
            recordedBlocks = Int(be32(b, o + 12))
            extents = (0..<8).map { Extent(start: Int(be32(b, o + 16 + 8 * $0)), count: Int(be32(b, o + 20 + 8 * $0))) }
                .filter { $0.count > 0 }
        }

        /// HFSPlusForkData (80 bytes): the first eight extents inline.
        func record() -> [UInt8] {
            var r = [UInt8](repeating: 0, count: 80)
            bePut64(&r, 0, logicalSize)
            bePut32(&r, 8, clumpSize)
            bePut32(&r, 12, UInt32(totalBlocks))
            for (i, e) in extents.prefix(8).enumerated() {
                bePut32(&r, 16 + 8 * i, UInt32(e.start))
                bePut32(&r, 20 + 8 * i, UInt32(e.count))
            }
            return r
        }
    }

    /// A folder or file record of the catalog.
    struct Item {
        var parent: UInt32
        var name: [UInt16]
        /// The record (HFSPlusCatalogFolder, 88 bytes, or HFSPlusCatalogFile, 248) as stored; forks are kept in
        /// `data` and `resource` and written into it at commit.
        var body: [UInt8]
        var data = Fork(), resource = Fork()
        var cnid: UInt32 { be32(body, 8) }
        var isFolder: Bool { be16(body, 0) == 1 }
        var mode: UInt16 { be16(body, 42) }
        var isSymlink: Bool { !isFolder && mode & 0o170000 == 0o120000 }
        var isHardLink: Bool { !isFolder && be32(body, 48) == 0x686C_6E6B && be32(body, 52) == 0x6866_732B }
        var compressed: Bool { body[41] & 0x20 != 0 }
    }

    /// An attributes B-tree record: its key fields and its body (inline data, fork data or overflow extents).
    struct Attribute {
        var fileID: UInt32
        var name: [UInt16]
        var startBlock: UInt32
        var body: [UInt8]
        /// The blocks a fork-data or extents record holds.
        var extents: [Extent] {
            switch be32(body, 0) {
            case 0x20: return Fork(body, 8).extents
            case 0x30:
                return (0..<8).map { Extent(start: Int(be32(body, 8 + 8 * $0)), count: Int(be32(body, 12 + 8 * $0))) }
                    .filter { $0.count > 0 }
            default: return []
            }
        }
    }

    /// What a B-tree's header record says of its shape, kept when the tree is written again.
    struct TreeShape {
        var nodeSize: Int
        var header: [UInt8]  // the 106-byte header record
        var variableIndexKeys: Bool { be32(header, 38) & 0x4 != 0 }
        var maxKeyLength: Int { Int(be16(header, 20)) }
    }

    static let rootID: UInt32 = 2
    /// Root items macOS's driver hides, kept as they are.
    static let hidden: Set<String> = [
        ".journal", ".journal_info_block", "\0\0\0\0HFS+ Private Data", ".HFS+ Private Directory Data\r",
        ".hotfiles.btree",
    ]
    /// A noowners mount's owner for what an edit adds (the Mac user's, as macOS records it).
    static let newOwner: (uid: UInt32, gid: UInt32) = (501, 20)

    let url: URL
    let fd: Int32
    var header: [UInt8]  // the 512-byte volume header
    let blockSize: Int
    let totalBlocks: Int
    var bitmap: [UInt8]
    var allocation: Fork, extentsFile: Fork, catalogFile: Fork, attributesFile: Fork
    var items: [UInt32: Item] = [:]
    var attributes: [Attribute] = []
    var catalogShape: TreeShape, attributesShape: TreeShape?, extentsShape: TreeShape
    var caseSensitive: Bool
    /// Host inode -> what extract wrote there.
    private var extracted: [UInt64: (cnid: UInt32, size: Int, mtime: timespec, link: String?)] = [:]
    /// A hard link's path -> its record and the copy extract wrote there (no host hard links: Android's SELinux
    /// refuses them to apps); commit keeps each only as it was (moving, removing or writing one is not supported).
    private var hardLinks: [String: (cnid: UInt32, inode: UInt64, size: Int, mtime: timespec)] = [:]
    private var reader: HFSPlusVolume?

    init(_ url: URL) throws {
        self.url = url
        fd = open(url.path, O_RDWR)
        guard fd >= 0 else { throw FirmwareError(.internal, "open \(url.path): \(String(cString: strerror(errno)))") }
        var vh = [UInt8](repeating: 0, count: 512)
        guard pread(fd, &vh, 512, 1024) == 512, vh[0] == 0x48, vh[1] == 0x2B || vh[1] == 0x58 else {
            close(fd)
            throw FirmwareError(.unsupported, "\(url.lastPathComponent): no HFS+ volume header")
        }
        header = vh
        blockSize = Int(be32(vh, 40))
        totalBlocks = Int(be32(vh, 44))
        allocation = Fork(vh, 112)
        extentsFile = Fork(vh, 192)
        catalogFile = Fork(vh, 272)
        attributesFile = Fork(vh, 352)
        bitmap = []
        catalogShape = TreeShape(nodeSize: 512, header: [])
        extentsShape = catalogShape
        caseSensitive = vh[1] == 0x58
        // Overflow extents first: the other files' forks may continue there.
        let overflow = try readOverflow()
        func complete(_ f: inout Fork, _ id: UInt32, _ type: UInt8) {
            guard f.totalBlocks < f.recordedBlocks else { return }
            f.extents += (overflow[UInt64(id) << 8 | UInt64(type)] ?? []).sorted { $0.0 < $1.0 }.flatMap(\.1)
        }
        complete(&allocation, 6, 0)
        complete(&catalogFile, 4, 0)
        complete(&attributesFile, 8, 0)
        bitmap = try read(allocation, count: (totalBlocks + 7) / 8)
        let (catShape, catRecords) = try leafRecords(catalogFile)
        catalogShape = catShape
        if let cs = catShape.header.count > 37 ? catShape.header[37] : nil, vh[1] == 0x58 { caseSensitive = cs == 0xBC }
        if !caseSensitive {
            // The folded order is rebuilt on commit: the volume's own must be that order, or the table is not Apple's.
            var last: (UInt32, [UInt16])?
            for r in catRecords {
                let key = (be32(r, 2), (0..<Int(be16(r, 6))).map { be16(r, 8 + 2 * $0) })
                if let (p, n) = last, p > key.0 || p == key.0 && HFSPlusCaseFold.compare(n, key.1) >= 0 {
                    throw FirmwareError(.unsupported, "\(url.lastPathComponent): catalog not in HFS+'s case-folded order")
                }
                last = key
            }
        }
        for r in catRecords {
            let keyLength = Int(be16(r, 0))
            var at = 2 + keyLength
            if at % 2 == 1 { at += 1 }
            let type = be16(r, at)
            guard type == 1 || type == 2 else { continue }
            let nameLength = Int(be16(r, 6))
            let body = Array(r[at..<min(r.count, at + (type == 1 ? 88 : 248))])
            var item = Item(
                parent: be32(r, 2),
                name: (0..<nameLength).map { be16(r, 8 + 2 * $0) },
                body: body
            )
            if type == 2 {
                item.data = Fork(body, 88)
                item.resource = Fork(body, 168)
                complete(&item.data, item.cnid, 0)
                complete(&item.resource, item.cnid, 0xFF)
            }
            items[item.cnid] = item
        }
        let (extShape, _) = try leafRecords(extentsFile)
        extentsShape = extShape
        if attributesFile.totalBlocks > 0 {
            let (attrShape, attrRecords) = try leafRecords(attributesFile)
            attributesShape = attrShape
            for r in attrRecords {
                let keyLength = Int(be16(r, 0))
                let nameLength = Int(be16(r, 12))
                attributes.append(
                    Attribute(
                        fileID: be32(r, 4),
                        name: (0..<nameLength).map { be16(r, 14 + 2 * $0) },
                        startBlock: be32(r, 8),
                        body: Array(r[(2 + keyLength)...])
                    )
                )
            }
        }
    }

    deinit { close(fd) }

    // MARK: - Reading

    func read(_ fork: Fork, count: Int) throws -> [UInt8] {
        var out = [UInt8](repeating: 0, count: count)
        var done = 0
        for e in fork.extents where done < count {
            let n = min(count - done, e.count * blockSize)
            let got = out.withUnsafeMutableBytes { pread(fd, $0.baseAddress! + done, n, off_t(e.start * blockSize)) }
            guard got == n else { throw FirmwareError(.unsupported, "\(url.lastPathComponent): short read") }
            done += n
        }
        return out
    }

    /// The overflow extents: (file ID << 8 | fork type) -> [(start block, extents)].
    private func readOverflow() throws -> [UInt64: [(Int, [Extent])]] {
        var out: [UInt64: [(Int, [Extent])]] = [:]
        guard extentsFile.totalBlocks > 0 else { return out }
        for r in try leafRecords(extentsFile).records where r.count >= 76 {
            let key = UInt64(be32(r, 4)) << 8 | UInt64(r[2])
            let extents = (0..<8).map { Extent(start: Int(be32(r, 12 + 8 * $0)), count: Int(be32(r, 16 + 8 * $0))) }
                .filter { $0.count > 0 }
            out[key, default: []].append((Int(be32(r, 8)), extents))
        }
        return out
    }

    /// A B-tree's header record and every leaf record (key and data), in order.
    private func leafRecords(_ fork: Fork) throws -> (shape: TreeShape, records: [[UInt8]]) {
        let file = try read(fork, count: fork.totalBlocks * blockSize)
        guard file.count >= 512 else { throw FirmwareError(.unsupported, "\(url.lastPathComponent): no B-tree") }
        let nodeSize = Int(be16(file, 14 + 18))
        let shape = TreeShape(nodeSize: nodeSize, header: Array(file[14..<120]))
        var records: [[UInt8]] = []
        var node = Int(be32(file, 14 + 10))
        var seen = Set<Int>()
        while node != 0 {
            guard seen.insert(node).inserted, (node + 1) * nodeSize <= file.count else {
                throw FirmwareError(.unsupported, "\(url.lastPathComponent): damaged B-tree at node \(node)")
            }
            let base = node * nodeSize
            let count = Int(be16(file, base + 10))
            let offsets = (0...count).map { Int(be16(file, base + nodeSize - 2 * ($0 + 1))) }
            for i in 0..<count { records.append(Array(file[base + offsets[i]..<base + offsets[i + 1]])) }
            node = Int(be32(file, base))
        }
        return (shape, records)
    }

    // MARK: - Paths

    /// Each visible item's path from the volume root ("a/b"), the root itself "" (hidden items and what they hold
    /// have none).
    func paths() -> [UInt32: String] {
        var out: [UInt32: String] = [Self.rootID: ""]
        var hidden = Set<UInt32>()
        func path(_ id: UInt32) -> String? {
            if let p = out[id] { return p }
            if hidden.contains(id) { return nil }
            guard let item = items[id], item.parent != 1 else { return nil }
            let name = String(decoding: item.name, as: UTF16.self)
            if item.parent == Self.rootID && Self.hidden.contains(name) {
                hidden.insert(id)
                return nil
            }
            guard let parent = path(item.parent) else {
                hidden.insert(id)
                return nil
            }
            let p = parent.isEmpty ? HFSPlusVolume.posixName(name) : parent + "/" + HFSPlusVolume.posixName(name)
            out[id] = p
            return p
        }
        for id in items.keys { _ = path(id) }
        return out
    }

    // MARK: - Extract

    /// Writes every visible file and folder into `root` (an empty or absent folder): contents decompressed,
    /// symlinks as symlinks, permission bits and dates from the catalog. `workers` threads write the files;
    /// `progress` gets the fraction done.
    func extract(to root: URL, workers: Int = 4, progress: (@Sendable (Double) -> Void)? = nil) throws {
        let fm = FileManager.default
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        let reader = try HFSPlusVolume(url)
        try reader.warmCaches()
        self.reader = reader
        var byID: [UInt32: HFSPlusVolume.CatalogRecord] = [:]
        for r in try reader.catalog() { byID[r.cnid] = r }
        let records = byID
        let paths = paths()
        let folders = paths.filter { items[$0.key]?.isFolder == true }.sorted { $0.value < $1.value }
        for (id, path) in folders where id != Self.rootID {
            guard mkdir(root.appendingPathComponent(path).path, 0o700) == 0 else {
                throw FirmwareError(.internal, "mkdir \(path): \(String(cString: strerror(errno)))")
            }
        }
        let files = paths.filter { items[$0.key]?.isFolder == false }.map { ($0.key, $0.value) }
        let total = files.reduce(0) { $0 + Int(items[$1.0]?.data.logicalSize ?? 0) } + 1
        let state = ExtractState()
        DispatchQueue.concurrentPerform(iterations: max(1, workers)) { worker in
            var i = worker
            while i < files.count, !state.failed {
                let (id, path) = files[i]
                do {
                    guard let item = items[id], let record = records[id] else {
                        throw FirmwareError(.internal, "\(path): no catalog record")
                    }
                    let target = root.appendingPathComponent(path).path
                    if item.isSymlink {
                        let link = String(decoding: try reader.contents(record), as: UTF8.self)
                        guard symlink(link, target) == 0 else {
                            throw FirmwareError(.internal, "symlink \(path): \(String(cString: strerror(errno)))")
                        }
                    } else {
                        let out = open(target, O_WRONLY | O_CREAT | O_EXCL, 0o600)
                        guard out >= 0 else {
                            throw FirmwareError(.internal, "create \(path): \(String(cString: strerror(errno)))")
                        }
                        defer { close(out) }
                        try reader.readContents(record) { slice in
                            try slice.withUnsafeBytes { b in
                                var done = 0
                                while done < b.count {
                                    let n = write(out, b.baseAddress! + done, b.count - done)
                                    guard n > 0 else {
                                        throw FirmwareError(.internal, "write \(path): \(String(cString: strerror(errno)))")
                                    }
                                    done += n
                                }
                            }
                        }
                        let mode = item.isHardLink ? try reader.inode(record).mode : item.mode
                        guard fchmod(out, mode_t(mode & 0o7777)) == 0 else {
                            throw FirmwareError(.internal, "chmod \(path): \(String(cString: strerror(errno)))")
                        }
                        Self.setTimes(out, nil, item.body)
                    }
                    let done = state.add(Int(item.data.logicalSize))
                    if worker == 0 { progress?(Double(done) / Double(total)) }
                } catch {
                    state.fail(error)
                }
                i += max(1, workers)
            }
        }
        if let error = state.error { throw error }
        for (id, path) in folders.reversed() {
            guard let item = items[id] else { continue }
            let target = root.appendingPathComponent(path).path
            guard chmod(target, mode_t(item.mode & 0o7777)) == 0 else {
                throw FirmwareError(.internal, "chmod \(path): \(String(cString: strerror(errno)))")
            }
            Self.setTimes(-1, target, item.body)
        }
        // What is there now, by inode: commit tells an untouched file from a written one, wherever it went.
        extracted = [:]
        hardLinks = [:]
        for (id, path) in files where items[id]?.isHardLink == true {
            var st = stat()
            guard lstat(root.appendingPathComponent(path).path, &st) == 0 else { continue }
            hardLinks[path] = (id, UInt64(st.st_ino), Int(st.st_size), Self.mtime(st))
        }
        for (id, path) in files where items[id]?.isHardLink != true {
            var st = stat()
            guard lstat(root.appendingPathComponent(path).path, &st) == 0 else { continue }
            let link = items[id]?.isSymlink == true
                ? (try? fm.destinationOfSymbolicLink(atPath: root.appendingPathComponent(path).path)) : nil
            extracted[UInt64(st.st_ino)] = (id, Int(st.st_size), Self.mtime(st), link)
        }
        for (id, path) in folders {
            var st = stat()
            guard lstat(root.appendingPathComponent(path).path, &st) == 0 else { continue }
            extracted[UInt64(st.st_ino)] = (id, 0, Self.mtime(st), nil)
        }
        progress?(1)
    }

    /// The record's access and content-modified dates on the host file (`fd`, else `path`).
    static func setTimes(_ fd: Int32, _ path: String?, _ body: [UInt8]) {
        let unix = { (hfs: UInt32) in timespec(tv_sec: Int(hfs) - 2_082_844_800, tv_nsec: 0) }
        var times = [unix(be32(body, 24)), unix(be32(body, 16))]
        if let path { _ = utimensat(AT_FDCWD, path, &times, 0) } else { _ = futimens(fd, &times) }
    }

    static func mtime(_ st: stat) -> timespec {
        #if canImport(Darwin)
        return st.st_mtimespec
        #else
        return st.st_mtim
        #endif
    }

    // MARK: - Commit

    /// Makes the volume hold the tree at `root` (as extract left it, then edited): see the file comment.
    func commit(from root: URL, now: Date = Date()) throws {
        let hfsNow = UInt32(clamping: Int(now.timeIntervalSince1970) + 2_082_844_800)
        let paths = paths()
        // What stays as it is: the root and the hidden items, with everything below them.
        var next: [UInt32: Item] = [:]
        for (id, item) in items where paths[id] == nil || id == Self.rootID { next[id] = item }
        var nextID = be32(header, 64)
        var freed: [Extent] = []
        var claimed = Set<UInt32>()
        var dropAttributes = Set<UInt32>()  // decmpfs of files written plain
        var written: [(cnid: UInt32, source: String)] = []

        func walk(_ dir: String, _ parent: UInt32) throws {
            // A folder its owner may not list (.Trashes is d-wx-wx-wx) is opened up: its mode was read already.
            let folder = root.appendingPathComponent(dir).path
            var dst = stat()
            if lstat(folder, &dst) == 0, dst.st_mode & 0o500 != 0o500 { chmod(folder, dst.st_mode & 0o7777 | 0o700) }
            let names = try FileManager.default.contentsOfDirectory(atPath: folder).sorted()
            for host in names {
                let rel = dir.isEmpty ? host : dir + "/" + host
                let full = root.appendingPathComponent(rel).path
                var st = stat()
                guard lstat(full, &st) == 0 else { throw FirmwareError(.internal, "lstat \(rel)") }
                let type = st.st_mode & S_IFMT
                guard type == S_IFDIR || type == S_IFREG || type == S_IFLNK else {
                    throw FirmwareError(.unsupported, "\(rel): not a file, folder or symlink")
                }
                let name = Array(HFSPlusVolume.catalogName(host).decomposedStringWithCanonicalMapping.utf16)
                if type == S_IFREG && st.st_nlink > 1 {
                    throw FirmwareError(.unsupported, "\(rel): a hard link (not supported here)")
                }
                if let was = hardLinks[rel] {
                    guard type == S_IFREG, UInt64(st.st_ino) == was.inode, Int(st.st_size) == was.size,
                        Self.mtime(st).tv_sec == was.mtime.tv_sec, Self.mtime(st).tv_nsec == was.mtime.tv_nsec,
                        var item = items[was.cnid], claimed.insert(was.cnid).inserted
                    else {
                        throw FirmwareError(.unsupported, "\(rel): a hard link replaced or written (not supported here)")
                    }
                    let cnid = was.cnid
                    item.parent = parent
                    item.name = name
                    next[cnid] = item
                    continue
                }
                // Linux has no symlink modes (always 777); macOS makes them 755.
                let perm = type == S_IFLNK ? 0o755 : UInt16(st.st_mode & 0o7777)
                var item: Item
                if let was = extracted[UInt64(st.st_ino)], let old = items[was.cnid],
                    old.isFolder == (type == S_IFDIR), old.isSymlink == (type == S_IFLNK),
                    claimed.insert(was.cnid).inserted
                {
                    item = old
                    item.parent = parent
                    item.name = name
                    if type != S_IFLNK { bePut16(&item.body, 42, old.mode & ~0o7777 | perm) }
                    if type == S_IFREG
                        && (Int(st.st_size) != was.size || Self.mtime(st).tv_sec != was.mtime.tv_sec
                            || Self.mtime(st).tv_nsec != was.mtime.tv_nsec)
                    {
                        // written: stored plain, as the driver stores a compressed file once written
                        freed += item.data.extents
                        item.data = Fork()
                        if item.compressed {
                            freed += item.resource.extents
                            item.resource = Fork()
                            item.body[41] &= ~0x20
                            dropAttributes.insert(item.cnid)
                        }
                        bePut32(&item.body, 16, Self.hfsDate(Self.mtime(st).tv_sec))
                        bePut32(&item.body, 20, hfsNow)
                        written.append((item.cnid, full))
                    } else if type == S_IFLNK {
                        let link = try FileManager.default.destinationOfSymbolicLink(atPath: full)
                        if link != was.link {
                            freed += item.data.extents
                            item.data = Fork()
                            bePut32(&item.body, 16, hfsNow)
                            written.append((item.cnid, full))
                        }
                    }
                } else {
                    let id = nextID
                    nextID += 1
                    item = Self.newItem(
                        cnid: id,
                        parent: parent,
                        name: name,
                        kind: type,
                        perm: perm,
                        date: Self.hfsDate(Self.mtime(st).tv_sec),
                        caseSensitive: caseSensitive
                    )
                    if type != S_IFDIR { written.append((id, full)) }
                }
                next[item.cnid] = item
                if type == S_IFDIR { try walk(rel, item.cnid) }
            }
        }
        if var rootItem = next[Self.rootID] {
            var st = stat()
            if lstat(root.path, &st) == 0 { bePut16(&rootItem.body, 42, rootItem.mode & ~0o7777 | UInt16(st.st_mode & 0o7777)) }
            next[Self.rootID] = rootItem
        }
        try walk("", Self.rootID)
        if let gone = hardLinks.first(where: { !claimed.contains($0.value.cnid) }) {
            throw FirmwareError(.unsupported, "\(gone.key): a hard link moved or removed (not supported here)")
        }

        // Gone: their blocks and attributes go with them.
        for (id, item) in items where next[id] == nil {
            freed += item.data.extents + item.resource.extents
            dropAttributes.insert(id)
        }
        var keptAttributes: [Attribute] = []
        for a in attributes {
            let gone = next[a.fileID] == nil
            let decompressed = dropAttributes.contains(a.fileID) && a.name == Array("com.apple.decmpfs".utf16)
            if gone || decompressed { freed += a.extents } else { keptAttributes.append(a) }
        }
        free(freed)
        zero(freed)

        // New contents: the host files' bytes into new blocks.
        for (cnid, source) in written {
            guard var item = next[cnid] else { continue }
            let bytes: [UInt8]? =
                item.isSymlink ? Array(try FileManager.default.destinationOfSymbolicLink(atPath: source).utf8) : nil
            var st = stat()
            let size = bytes?.count ?? (lstat(source, &st) == 0 ? Int(st.st_size) : 0)
            item.data = Fork()
            item.data.logicalSize = UInt64(size)
            item.data.extents = try allocate((size + blockSize - 1) / blockSize)
            if let bytes { try writeFork(item.data, bytes: bytes) } else { try writeFork(item.data, from: source) }
            next[cnid] = item
        }

        items = next
        attributes = keptAttributes
        bePut32(&header, 64, nextID)
        try writeMetadata(hfsNow: hfsNow)
    }

    static func hfsDate(_ unix: Int) -> UInt32 { UInt32(clamping: unix + 2_082_844_800) }

    /// A new folder, file or symlink record, owned as a noowners mount records it.
    static func newItem(
        cnid: UInt32,
        parent: UInt32,
        name: [UInt16],
        kind: mode_t,
        perm: UInt16,
        date: UInt32,
        caseSensitive: Bool
    ) -> Item {
        let folder = kind == S_IFDIR
        var b = [UInt8](repeating: 0, count: folder ? 88 : 248)
        bePut16(&b, 0, folder ? 1 : 2)
        bePut16(&b, 2, folder ? (caseSensitive ? 0x10 : 0) : 0x2)  // folder count / thread exists
        bePut32(&b, 8, cnid)
        for at in [12, 16, 20, 24] { bePut32(&b, at, date) }
        bePut32(&b, 32, newOwner.uid)
        bePut32(&b, 36, newOwner.gid)
        bePut16(&b, 42, UInt16(kind) | perm)
        if kind == S_IFLNK {
            bePut32(&b, 48, 0x736C_6E6B)  // 'slnk'
            bePut32(&b, 52, 0x7268_6170)  // 'rhap'
        }
        return Item(parent: parent, name: name, body: b)
    }

    // MARK: - Blocks

    func isUsed(_ block: Int) -> Bool { bitmap[block >> 3] & (0x80 >> UInt8(block & 7)) != 0 }

    func mark(_ e: Extent, used: Bool) {
        for b in e.start..<e.start + e.count {
            if used { bitmap[b >> 3] |= 0x80 >> UInt8(b & 7) } else { bitmap[b >> 3] &= ~(0x80 >> UInt8(b & 7)) }
        }
    }

    func free(_ extents: [Extent]) { for e in extents { mark(e, used: false) } }

    /// Zeroes `extents` on disk (punching holes where the host can), so freed blocks pack as empty pages.
    func zero(_ extents: [Extent]) {
        let zeros = [UInt8](repeating: 0, count: 1 << 20)
        for e in extents {
            let start = e.start * blockSize, length = e.count * blockSize
            if punchHole(fd, offset: start, length: length) == 0 { continue }
            var done = 0
            while done < length {
                let n = min(zeros.count, length - done)
                _ = pwrite(fd, zeros, n, off_t(start + done))
                done += n
            }
        }
    }

    /// `count` free blocks, as few extents as there are runs for (first fit).
    func allocate(_ count: Int, near hint: Int = 0) throws -> [Extent] {
        guard count > 0 else { return [] }
        var runs: [Extent] = []
        var b = 0
        while b < totalBlocks {
            if bitmap[b >> 3] == 0xFF && b & 7 == 0 {
                b += 8
                continue
            }
            if isUsed(b) {
                b += 1
                continue
            }
            var e = b
            while e < totalBlocks && !isUsed(e) && e - b < count { e += 1 }
            if e - b == count {
                let hit = Extent(start: b, count: count)
                mark(hit, used: true)
                return [hit]
            }
            runs.append(Extent(start: b, count: e - b))
            b = e
        }
        // No single run: the largest ones.
        var out: [Extent] = []
        var left = count
        for r in runs.sorted(by: { $0.count > $1.count }) where left > 0 {
            let take = Extent(start: r.start, count: min(r.count, left))
            out.append(take)
            left -= take.count
        }
        guard left == 0 else {
            throw FirmwareError(.unsupported, "\(url.lastPathComponent): the volume is full (\(count) blocks wanted)")
        }
        for e in out { mark(e, used: true) }
        return out.sorted { $0.start < $1.start }
    }

    /// Writes `bytes` into `fork`'s blocks, zero-filling the rest of its last block.
    func writeFork(_ fork: Fork, bytes: [UInt8]) throws {
        var done = 0
        for e in fork.extents {
            let length = e.count * blockSize
            var chunk = [UInt8](repeating: 0, count: length)
            let n = max(0, min(length, bytes.count - done))
            if n > 0 { chunk.replaceSubrange(0..<n, with: bytes[done..<done + n]) }
            guard pwrite(fd, chunk, length, off_t(e.start * blockSize)) == length else {
                throw FirmwareError(.internal, "write \(url.lastPathComponent): \(String(cString: strerror(errno)))")
            }
            done += n
        }
    }

    /// Streams the host file `source` into `fork`'s blocks.
    func writeFork(_ fork: Fork, from source: String) throws {
        let input = open(source, O_RDONLY)
        guard input >= 0 else { throw FirmwareError(.internal, "open \(source): \(String(cString: strerror(errno)))") }
        defer { close(input) }
        var buffer = [UInt8](repeating: 0, count: 1 << 20)
        for e in fork.extents {
            var at = 0
            let length = e.count * blockSize
            while at < length {
                let want = min(buffer.count, length - at)
                var got = 0
                while got < want {
                    let n = buffer.withUnsafeMutableBytes { readFile(input, $0.baseAddress! + got, want - got) }
                    if n <= 0 { break }
                    got += n
                }
                for k in got..<want { buffer[k] = 0 }  // past the host file's end
                guard pwrite(fd, buffer, want, off_t(e.start * blockSize + at)) == want else {
                    throw FirmwareError(.internal, "write \(url.lastPathComponent): \(String(cString: strerror(errno)))")
                }
                at += want
            }
        }
    }

    // MARK: - B-trees and the header

    /// Writes the catalog, attributes and extents overflow B-trees, the allocation bitmap and the volume headers.
    func writeMetadata(hfsNow: UInt32) throws {
        // Folder valences and folder counts; the has-attributes flag.
        var valence: [UInt32: UInt32] = [:]
        var subfolders: [UInt32: UInt32] = [:]
        for item in items.values where item.cnid != Self.rootID {
            valence[item.parent, default: 0] += 1
            if item.isFolder { subfolders[item.parent, default: 0] += 1 }
        }
        let withAttributes = Set(attributes.map(\.fileID))
        for (id, var item) in items {
            if item.isFolder {
                bePut32(&item.body, 4, valence[id] ?? 0)
                if be16(item.body, 2) & 0x10 != 0 { bePut32(&item.body, 84, subfolders[id] ?? 0) }
            }
            let flags = be16(item.body, 2)
            bePut16(&item.body, 2, withAttributes.contains(id) ? flags | 0x4 : flags & ~0x4)
            items[id] = item
        }
        // Catalog: every record and its thread, by (parent, name) in binary order (HFSX).
        var catalog: [(parent: UInt32, name: [UInt16], record: [UInt8])] = []
        var files = 0, folders = 0
        for item in items.values {
            var body = item.body
            if !item.isFolder {
                body.replaceSubrange(88..<168, with: item.data.record())
                body.replaceSubrange(168..<248, with: item.resource.record())
                files += 1
            } else if item.cnid != Self.rootID {
                folders += 1
            }
            catalog.append((item.parent, item.name, Self.catalogKey(item.parent, item.name) + body))
            var thread = [UInt8](repeating: 0, count: 10 + 2 * item.name.count)
            bePut16(&thread, 0, item.isFolder ? 3 : 4)
            bePut32(&thread, 4, item.parent)
            bePut16(&thread, 8, UInt16(item.name.count))
            for (i, u) in item.name.enumerated() { bePut16(&thread, 10 + 2 * i, u) }
            catalog.append((item.cnid, [], Self.catalogKey(item.cnid, []) + thread))
        }
        if caseSensitive {
            catalog.sort { $0.parent != $1.parent ? $0.parent < $1.parent : $0.name.lexicographicallyPrecedes($1.name) }
        } else {
            catalog.sort { $0.parent != $1.parent ? $0.parent < $1.parent : HFSPlusCaseFold.compare($0.name, $1.name) < 0 }
            for (a, b) in zip(catalog, catalog.dropFirst()) where a.parent == b.parent && HFSPlusCaseFold.compare(a.name, b.name) == 0 {
                throw FirmwareError(
                    .unsupported, "\(url.lastPathComponent): two names in one folder differ only in case (\(String(decoding: b.name, as: UTF16.self)))")
            }
        }
        try writeTree(catalog.map(\.record), shape: catalogShape, fork: &catalogFile, fileID: 4)

        if let shape = attributesShape {
            let sorted = attributes.sorted {
                $0.fileID != $1.fileID
                    ? $0.fileID < $1.fileID
                    : $0.name != $1.name ? $0.name.lexicographicallyPrecedes($1.name) : $0.startBlock < $1.startBlock
            }
            let records: [[UInt8]] = sorted.map { a in
                var key = [UInt8](repeating: 0, count: 14 + 2 * a.name.count)
                bePut16(&key, 0, UInt16(12 + 2 * a.name.count))
                bePut32(&key, 4, a.fileID)
                bePut32(&key, 8, a.startBlock)
                bePut16(&key, 12, UInt16(a.name.count))
                for (i, u) in a.name.enumerated() { bePut16(&key, 14 + 2 * i, u) }
                var body = a.body
                if body.count % 2 == 1 { body.append(0) }
                return key + body
            }
            try writeTree(records, shape: shape, fork: &attributesFile, fileID: 8)
        }

        // Extents overflow: every fork's extents past its eighth, as (file, fork type, start block) records.
        var overflow: [(id: UInt32, type: UInt8, start: UInt32, record: [UInt8])] = []
        func spill(_ fork: Fork, _ id: UInt32, _ type: UInt8) {
            guard fork.extents.count > 8 else { return }
            var start = fork.extents.prefix(8).reduce(0) { $0 + $1.count }
            var rest = Array(fork.extents.dropFirst(8))
            while !rest.isEmpty {
                let group = Array(rest.prefix(8))
                rest.removeFirst(group.count)
                var r = [UInt8](repeating: 0, count: 12 + 64)
                bePut16(&r, 0, 10)
                r[2] = type
                bePut32(&r, 4, id)
                bePut32(&r, 8, UInt32(start))
                for (i, e) in group.enumerated() {
                    bePut32(&r, 12 + 8 * i, UInt32(e.start))
                    bePut32(&r, 16 + 8 * i, UInt32(e.count))
                }
                overflow.append((id, type, UInt32(start), r))
                start += group.reduce(0) { $0 + $1.count }
            }
        }
        for item in items.values where !item.isFolder {
            spill(item.data, item.cnid, 0)
            spill(item.resource, item.cnid, 0xFF)
        }
        spill(allocation, 6, 0)
        spill(catalogFile, 4, 0)
        spill(attributesFile, 8, 0)
        overflow.sort { ($0.id, $0.type, $0.start) < ($1.id, $1.type, $1.start) }
        try writeTree(overflow.map(\.record), shape: extentsShape, fork: &extentsFile, fileID: 3)
        guard extentsFile.extents.count <= 8 else {
            throw FirmwareError(.unsupported, "\(url.lastPathComponent): the extents file is too fragmented")
        }

        // The bitmap, then the header (counts, forks) at 1024 and the alternate at the end.
        var map = bitmap
        let mapBytes = allocation.totalBlocks * blockSize
        if map.count < mapBytes { map += [UInt8](repeating: 0, count: mapBytes - map.count) }
        try writeFork(allocation, bytes: map)
        var used = 0
        for b in 0..<totalBlocks where isUsed(b) { used += 1 }
        bePut32(&header, 4, be32(header, 4) & ~0x800 | 0x100)  // unmounted, not inconsistent
        bePut32(&header, 20, hfsNow)
        bePut32(&header, 32, UInt32(files))
        bePut32(&header, 36, UInt32(folders))
        bePut32(&header, 48, UInt32(totalBlocks - used))
        bePut32(&header, 68, be32(header, 68) &+ 1)
        header.replaceSubrange(112..<192, with: allocation.record())
        header.replaceSubrange(192..<272, with: extentsFile.record())
        header.replaceSubrange(272..<352, with: catalogFile.record())
        header.replaceSubrange(352..<432, with: attributesFile.record())
        try writeHeaders()
    }

    func writeHeaders() throws {
        var st = stat()
        guard fstat(fd, &st) == 0 else { throw FirmwareError(.internal, "stat \(url.lastPathComponent)") }
        let volumeEnd = Int(be32(header, 44)) * blockSize
        for at in Set([1024, volumeEnd - 1024, Int(st.st_size) - 1024]) where at >= volumeEnd - 1024 || at == 1024 {
            guard pwrite(fd, header, 512, off_t(at)) == 512 else {
                throw FirmwareError(.internal, "write the volume header of \(url.lastPathComponent)")
            }
        }
    }

    static func catalogKey(_ parent: UInt32, _ name: [UInt16]) -> [UInt8] {
        var key = [UInt8](repeating: 0, count: 8 + 2 * name.count)
        bePut16(&key, 0, UInt16(6 + 2 * name.count))
        bePut32(&key, 2, parent)
        bePut16(&key, 6, UInt16(name.count))
        for (i, u) in name.enumerated() { bePut16(&key, 8 + 2 * i, u) }
        return key
    }

    /// Writes a B-tree of `records` (sorted; each its key, from its keyLength field, then its data) into `fork`,
    /// growing the file by whole clumps when its nodes no longer fit.
    func writeTree(_ records: [[UInt8]], shape: TreeShape, fork: inout Fork, fileID: UInt32) throws {
        let nodeSize = shape.nodeSize
        let room = nodeSize - 14 - 2
        // Leaves, then index levels until one node holds them all.
        var levels: [[(records: [[UInt8]], firstKey: [UInt8])]] = []
        func pack(_ recs: [[UInt8]]) -> [(records: [[UInt8]], firstKey: [UInt8])] {
            var nodes: [(records: [[UInt8]], firstKey: [UInt8])] = []
            var current: [[UInt8]] = []
            var used = 0
            for r in recs {
                if !current.isEmpty && used + r.count + 2 > room {
                    nodes.append((current, current[0]))
                    current = []
                    used = 0
                }
                current.append(r)
                used += r.count + 2
            }
            if !current.isEmpty { nodes.append((current, current[0])) }
            return nodes
        }
        if !records.isEmpty {
            levels.append(pack(records))
            var number = 1 + levels[0].count  // index node numbers follow the leaves
            var firstNumbers = Array(1...levels[0].count)
            while levels.last!.count > 1 {
                let below = levels.last!
                let index: [[UInt8]] = zip(below, firstNumbers).map { node, n in
                    var key = Array(node.firstKey.prefix(2 + Int(be16(node.firstKey, 0))))
                    if !shape.variableIndexKeys {
                        key += [UInt8](repeating: 0, count: max(0, shape.maxKeyLength + 2 - key.count))
                        bePut16(&key, 0, UInt16(shape.maxKeyLength))
                    }
                    var ptr = [UInt8](repeating: 0, count: 4)
                    bePut32(&ptr, 0, UInt32(n))
                    return key + ptr
                }
                let level = pack(index)
                firstNumbers = Array(number..<number + level.count)
                number += level.count
                levels.append(level)
            }
        }
        let treeNodes = 1 + levels.reduce(0) { $0 + $1.count }
        let headerMapBits = (nodeSize - 256) * 8
        let mapNodeBits = (nodeSize - 14 - 4) * 8
        // Room: the file's nodes, grown by clumps with a quarter spare when the tree outgrows them.
        var capacity = fork.totalBlocks * blockSize / nodeSize
        func mapNodes(_ total: Int) -> Int { total <= headerMapBits ? 0 : (total - headerMapBits + mapNodeBits - 1) / mapNodeBits }
        if treeNodes + mapNodes(capacity) > capacity {
            let want = (treeNodes + treeNodes / 4 + 8) * nodeSize
            let clump = max(Int(fork.clumpSize), blockSize)
            let grow = ((want - capacity * nodeSize + clump - 1) / clump) * clump / blockSize
            fork.extents += try allocate(grow, near: fork.extents.last.map { $0.start + $0.count } ?? 0)
            capacity = fork.totalBlocks * blockSize / nodeSize
        }
        let totalNodes = capacity
        let maps = mapNodes(totalNodes)
        let used = treeNodes + maps
        guard used <= totalNodes else { throw FirmwareError(.internal, "B-tree \(fileID) does not fit") }
        fork.logicalSize = UInt64(totalNodes * nodeSize)

        var file = [UInt8](repeating: 0, count: totalNodes * nodeSize)
        func node(_ n: Int, kind: UInt8, height: UInt8, _ recs: [[UInt8]], fLink: Int, bLink: Int) {
            let base = n * nodeSize
            bePut32(&file, base, UInt32(fLink))
            bePut32(&file, base + 4, UInt32(bLink))
            file[base + 8] = kind
            file[base + 9] = height
            bePut16(&file, base + 10, UInt16(recs.count))
            var at = 14
            for (i, r) in recs.enumerated() {
                bePut16(&file, base + nodeSize - 2 * (i + 1), UInt16(at))
                file.replaceSubrange(base + at..<base + at + r.count, with: r)
                at += r.count
            }
            bePut16(&file, base + nodeSize - 2 * (recs.count + 1), UInt16(at))
        }
        var number = 1
        var root = 0, firstLeaf = 0, lastLeaf = 0
        for (depth, level) in levels.enumerated() {
            let first = number
            for (i, n) in level.enumerated() {
                node(
                    first + i,
                    kind: depth == 0 ? 0xFF : 0,
                    height: UInt8(depth + 1),
                    n.records,
                    fLink: i + 1 < level.count ? first + i + 1 : 0,
                    bLink: i > 0 ? first + i - 1 : 0
                )
            }
            if depth == 0 { (firstLeaf, lastLeaf) = (first, first + level.count - 1) }
            number += level.count
            root = first
        }
        // Map: bits for the used nodes (the header, the tree, the map nodes), MSB first.
        var bits = [UInt8](repeating: 0, count: (totalNodes + 7) / 8)
        for n in 0..<used { bits[n >> 3] |= 0x80 >> UInt8(n & 7) }
        var hdr = shape.header
        bePut16(&hdr, 0, UInt16(levels.count))
        bePut32(&hdr, 2, UInt32(root))
        bePut32(&hdr, 6, UInt32(records.count))
        bePut32(&hdr, 10, UInt32(firstLeaf))
        bePut32(&hdr, 14, UInt32(lastLeaf))
        bePut32(&hdr, 22, UInt32(totalNodes))
        bePut32(&hdr, 26, UInt32(totalNodes - used))
        let headerMap = Array(bits.prefix(nodeSize - 256)) + [UInt8](repeating: 0, count: max(0, nodeSize - 256 - bits.count))
        node(0, kind: 1, height: 0, [hdr, [UInt8](repeating: 0, count: 128), headerMap], fLink: maps > 0 ? treeNodes : 0, bLink: 0)
        for m in 0..<maps {
            let from = nodeSize - 256 + m * (nodeSize - 18)
            var chunk = Array(bits.dropFirst(from).prefix(nodeSize - 18))
            chunk += [UInt8](repeating: 0, count: nodeSize - 18 - chunk.count)
            node(treeNodes + m, kind: 2, height: 0, [chunk], fLink: m + 1 < maps ? treeNodes + m + 1 : 0, bLink: 0)
        }
        try writeFork(fork, bytes: file)
    }

    // MARK: - Grow and format

    /// Grows the volume in `image` to `bytes` (whole blocks; the file grows with it): the new blocks free, the
    /// alternate header moved to the new end.
    static func grow(_ image: URL, toBytes bytes: Int) throws {
        let editor = try HFSPlusEditor(image)
        try editor.grow(toBytes: bytes)
    }

    private func grow(toBytes bytes: Int) throws {
        var st = stat()
        guard fstat(fd, &st) == 0 else { throw FirmwareError(.internal, "stat \(url.lastPathComponent)") }
        let oldSize = Int(st.st_size)
        let newTotal = bytes / blockSize
        guard newTotal >= totalBlocks else { throw FirmwareError(.internal, "\(url.lastPathComponent): cannot shrink") }
        guard ftruncate(fd, off_t(bytes)) == 0 else {
            throw FirmwareError(.internal, "grow \(url.lastPathComponent): \(String(cString: strerror(errno)))")
        }
        var map = bitmap + [UInt8](repeating: 0, count: max(0, (newTotal + 7) / 8 - bitmap.count))
        // The alternate header sits 1024 bytes before the volume's end, in its last block, which is kept allocated:
        // the old last block comes free, the new one is taken. (A file may run past the volume: the iPod touch
        // 4G's 6.1.6 system volume is 4 KiB short of its partition.)
        if newTotal > totalBlocks {
            let oldTail = totalBlocks - 1, newTail = newTotal - 1
            map[oldTail >> 3] &= ~(0x80 >> UInt8(oldTail & 7))
            map[newTail >> 3] |= 0x80 >> UInt8(newTail & 7)
        }
        var zeros = [UInt8](repeating: 0, count: 1024)
        for at in Set([totalBlocks * blockSize - 1024, oldSize - 1024]) { _ = pwrite(fd, &zeros, 1024, off_t(at)) }
        // The bitmap must cover the new blocks: the allocation file grows into the new space if it is too short.
        if allocation.totalBlocks * blockSize < map.count {
            let more = (map.count + blockSize - 1) / blockSize - allocation.totalBlocks
            var start = totalBlocks
            while start < newTotal && map[start >> 3] & (0x80 >> UInt8(start & 7)) != 0 { start += 1 }
            guard start + more <= newTotal else { throw FirmwareError(.unsupported, "no room for the bitmap") }
            for b in start..<start + more { map[b >> 3] |= 0x80 >> UInt8(b & 7) }
            allocation.extents.append(Extent(start: start, count: more))
            allocation.logicalSize = UInt64(allocation.totalBlocks * blockSize)
            guard allocation.extents.count <= 8 else { throw FirmwareError(.unsupported, "fragmented bitmap") }
        }
        let mapBytes = allocation.totalBlocks * blockSize
        try writeFork(allocation, bytes: map + [UInt8](repeating: 0, count: max(0, mapBytes - map.count)))
        var used = 0
        for b in 0..<newTotal where map[b >> 3] & (0x80 >> UInt8(b & 7)) != 0 { used += 1 }
        bePut32(&header, 44, UInt32(newTotal))
        bePut32(&header, 48, UInt32(newTotal - used))
        header.replaceSubrange(112..<192, with: allocation.record())
        try writeHeaders()
    }

    /// A new, empty HFSX volume (case-sensitive; journaled with the journal left for the device's kernel to
    /// initialize, as newfs_hfs -J leaves one) in a sparse file of `size` bytes, rounded down to 4 KiB.
    static func format(_ image: URL, size: Int64, name: String, journaled: Bool, now: Date = Date()) throws {
        let blockSize = 4096
        let total = Int(size) / blockSize
        guard total > 64 else { throw FirmwareError(.internal, "\(image.lastPathComponent): too small for HFS+") }
        let fd = open(image.path, O_RDWR | O_CREAT | O_TRUNC, 0o644)
        guard fd >= 0, ftruncate(fd, off_t(total * blockSize)) == 0 else {
            if fd >= 0 { close(fd) }
            throw FirmwareError(.internal, "cannot create \(image.path)")
        }
        defer { close(fd) }
        let date = hfsDate(Int(now.timeIntervalSince1970))
        // Layout: block 0 (boot blocks, the header), the bitmap, the extents, catalog and attributes files, the
        // journal info block and the journal; the last block holds the alternate header.
        var next = 1
        func take(_ blocks: Int) -> Extent {
            defer { next += blocks }
            return Extent(start: next, count: blocks)
        }
        let mapBlocks = ((total + 7) / 8 + blockSize - 1) / blockSize
        var allocation = Fork()
        allocation.extents = [take(mapBlocks)]
        allocation.logicalSize = UInt64(mapBlocks * blockSize)
        allocation.clumpSize = UInt32(blockSize)
        func treeFork(_ bytes: Int) -> Fork {
            var f = Fork()
            f.extents = [take(bytes / blockSize)]
            f.logicalSize = UInt64(bytes)
            f.clumpSize = UInt32(bytes)
            return f
        }
        let mib = 1 << 20
        let extents = treeFork(mib), catalog = treeFork(4 * mib), attributes = treeFork(mib)
        let journalBytes = 8 * mib
        let jib = journaled ? take(1) : nil
        let journal = journaled ? take(journalBytes / blockSize) : nil
        guard next < total - 1 else { throw FirmwareError(.internal, "\(image.lastPathComponent): too small for HFS+") }

        var vh = [UInt8](repeating: 0, count: 512)
        vh[0] = 0x48
        vh[1] = 0x58  // HX
        bePut16(&vh, 2, 5)
        bePut32(&vh, 4, 0x100 | (journaled ? 0x2000 : 0))  // unmounted, journaled
        bePut32(&vh, 8, journaled ? 0x4846_534A : 0x3130_2E30)  // 'HFSJ' / '10.0'
        bePut32(&vh, 12, UInt32(jib?.start ?? 0))
        for at in [16, 20, 24, 28] { bePut32(&vh, at, date) }  // create, modify, backup, checked
        bePut32(&vh, 40, UInt32(blockSize))
        bePut32(&vh, 44, UInt32(total))
        bePut32(&vh, 56, UInt32(blockSize))  // rsrcClumpSize
        bePut32(&vh, 60, UInt32(blockSize))  // dataClumpSize
        bePut32(&vh, 64, 16)  // nextCatalogID
        bePut32(&vh, 72, 0)
        bePut32(&vh, 76, 1)  // encodingsBitmap: MacRoman
        // empty trees, one header node each, written by an editor over this header
        func emptyTree(_ fork: Fork, nodeSize: Int, maxKey: Int, compare: UInt8, attributes: UInt32) throws {
            var hdr = [UInt8](repeating: 0, count: 106)
            bePut16(&hdr, 18, UInt16(nodeSize))
            bePut16(&hdr, 20, UInt16(maxKey))
            bePut32(&hdr, 32, fork.clumpSize)
            hdr[36] = 0  // btreeType: HFS
            hdr[37] = compare
            bePut32(&hdr, 38, attributes)
            var node = [UInt8](repeating: 0, count: nodeSize)
            node[8] = 1
            bePut16(&node, 10, 3)
            node.replaceSubrange(14..<120, with: hdr)
            for (i, at) in [14, 120, 248, nodeSize - 8].enumerated() { bePut16(&node, nodeSize - 2 * (i + 1), UInt16(at)) }
            guard pwrite(fd, node, nodeSize, off_t(fork.extents[0].start * blockSize)) == nodeSize else {
                throw FirmwareError(.internal, "write \(image.lastPathComponent)")
            }
        }
        try emptyTree(extents, nodeSize: 4096, maxKey: 10, compare: 0, attributes: 0x2)
        try emptyTree(catalog, nodeSize: 8192, maxKey: 516, compare: 0xBC, attributes: 0x6)
        try emptyTree(attributes, nodeSize: 8192, maxKey: 266, compare: 0, attributes: 0x6)
        vh.replaceSubrange(112..<192, with: allocation.record())
        vh.replaceSubrange(192..<272, with: extents.record())
        vh.replaceSubrange(272..<352, with: catalog.record())
        vh.replaceSubrange(352..<432, with: attributes.record())
        guard pwrite(fd, vh, 512, 1024) == 512, pwrite(fd, vh, 512, off_t(total * blockSize - 1024)) == 512 else {
            throw FirmwareError(.internal, "write \(image.lastPathComponent)")
        }
        if let jib, let journal {
            // flags: in the file system, needs init; then device_signature[8], offset, size
            var info = [UInt8](repeating: 0, count: blockSize)
            bePut32(&info, 0, 0x1 | 0x4)
            bePut64(&info, 36, UInt64(journal.start * blockSize))
            bePut64(&info, 44, UInt64(journalBytes))
            guard pwrite(fd, info, blockSize, off_t(jib.start * blockSize)) == blockSize else {
                throw FirmwareError(.internal, "write \(image.lastPathComponent)")
            }
        }

        // The rest through an editor: the bitmap, the root folder and the journal's files.
        let editor = try HFSPlusEditor(image)
        for e in [Extent(start: 0, count: next), Extent(start: total - 1, count: 1)] { editor.mark(e, used: true) }
        var rootItem = newItem(
            cnid: rootID, parent: 1, name: Array(name.utf16), kind: S_IFDIR, perm: 0o755, date: date, caseSensitive: true)
        bePut32(&rootItem.body, 32, 0)
        bePut32(&rootItem.body, 36, 0)
        editor.items[rootID] = rootItem
        if let jib, let journal {
            for (cnid, fileName, extent, bytes) in [
                (UInt32(16), ".journal", journal, journalBytes), (17, ".journal_info_block", jib, blockSize),
            ] {
                var f = newItem(
                    cnid: cnid, parent: rootID, name: Array(fileName.utf16), kind: S_IFREG, perm: 0, date: date,
                    caseSensitive: true)
                bePut32(&f.body, 32, 0)
                bePut32(&f.body, 36, 0)
                f.body[41] = 0  // ownerFlags
                f.body[40] = 0
                bePut32(&f.body, 48, 0x6A72_6E6C)  // 'jrnl'
                bePut32(&f.body, 52, 0x6866_732B)  // 'hfs+'
                bePut16(&f.body, 56, 0x5000)  // invisible, name locked
                f.data.extents = [extent]
                f.data.logicalSize = UInt64(bytes)
                editor.items[cnid] = f
            }
            bePut32(&editor.header, 64, 18)
        }
        try editor.writeMetadata(hfsNow: date)
    }
}

private final class ExtractState: @unchecked Sendable {
    private let lock = NSLock()
    private var done = 0
    private(set) var error: Error?
    var failed: Bool { lock.withLock { error != nil } }
    func add(_ n: Int) -> Int { lock.withLock { done += n; return done } }
    func fail(_ e: Error) { lock.withLock { if error == nil { error = e } } }
}

private func be16(_ b: [UInt8], _ o: Int) -> UInt16 { UInt16(b[o]) << 8 | UInt16(b[o + 1]) }
private func be64(_ b: [UInt8], _ o: Int) -> UInt64 { UInt64(be32(b, o)) << 32 | UInt64(be32(b, o + 4)) }
private func bePut16(_ b: inout [UInt8], _ o: Int, _ v: UInt16) {
    b[o] = UInt8(v >> 8)
    b[o + 1] = UInt8(v & 0xFF)
}
private func bePut32(_ b: inout [UInt8], _ o: Int, _ v: UInt32) {
    for i in 0..<4 { b[o + i] = UInt8(truncatingIfNeeded: v >> (24 - 8 * i)) }
}
private func bePut64(_ b: inout [UInt8], _ o: Int, _ v: UInt64) {
    bePut32(&b, o, UInt32(v >> 32))
    bePut32(&b, o + 4, UInt32(v & 0xFFFF_FFFF))
}

/// The C library's read(2), out of reach by name inside HFSPlusEditor (its own `read`).
private func readFile(_ fd: Int32, _ buffer: UnsafeMutableRawPointer, _ count: Int) -> Int { read(fd, buffer, count) }

/// HFSPlusVolume's reads are safe from several threads once its caches are loaded (warmCaches), as extract does.
extension HFSPlusVolume: @unchecked Sendable {}

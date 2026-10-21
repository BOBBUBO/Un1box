using System.Collections.Concurrent;
using System.Buffers.Binary;
using System.Runtime.ExceptionServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace LightTouchWin.Core;

// Transition adapter for Portable/src/Storage.cpp validate_base. Keep the
// authoritative lock/listing algorithm identical until portable I/O supports
// Windows. This verifies all bytes; cheap UI metadata never authorizes Start.
public static class BaseAdmission
{
    public static void Verify(string directory, Action<long, long>? progress = null,
                              Action<string>? checkPath = null, Func<string, Stream>? openRead = null,
                              int maxParallelism = 1)
    {
        if (maxParallelism is < 1 or > 8) throw new ArgumentOutOfRangeException(nameof(maxParallelism));
        directory = Path.GetFullPath(directory);
        void Check(string path)
        {
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidDataException("Prepared assets cannot contain links or junctions: " + path);
            checkPath?.Invoke(path);
        }
        Stream Open(string path) { Check(path); return openRead?.Invoke(path) ?? File.OpenRead(path); }
        var lockPath = Path.Combine(directory, "device.lock.json");
        using var lockStream = Open(lockPath);
        if (lockStream.Length > 1024 * 1024) throw new InvalidDataException("Device lock exceeds 1 MiB.");
        using var document = JsonDocument.Parse(lockStream);
        var root = document.RootElement;
        if (root.GetProperty("format").GetInt32() != 1 ||
            !Boards.All.ContainsKey(root.GetProperty("board").GetString() ?? "") ||
            string.IsNullOrWhiteSpace(root.GetProperty("build").GetString()))
            throw new InvalidDataException("Unsupported prepared base identity.");
        var outputs = root.GetProperty("outputs");
        static string Digest(JsonElement record, string name)
        {
            string value = record.GetProperty(name).GetString() ?? "";
            if (value.Length != 64 || value.Any(c => !char.IsAsciiHexDigit(c)))
                throw new InvalidDataException("Missing authoritative SHA-256 integrity record.");
            return value.ToLowerInvariant();
        }
        void Require(string key, string name, bool listing = false)
        {
            var record = outputs.GetProperty(key);
            if (record.GetProperty("path").GetString() != name)
                throw new InvalidDataException("Prepared base requires authoritative " + name + " integrity.");
            _ = Digest(record, listing ? "listing_sha256" : "sha256");
        }
        var nandRecord = outputs.GetProperty("nand");
        bool packedNand = nandRecord.TryGetProperty("format", out var nandFormat) && nandFormat.GetString() == "ITNAND01";
        long packedPages = 0;
        if (packedNand)
        {
            if (root.GetProperty("board").GetString() != "n72ap" || nandRecord.TryGetProperty("listing_sha256", out _) ||
                !nandRecord.TryGetProperty("pages", out var pages) || !pages.TryGetInt64(out packedPages) ||
                packedPages is < 0 or > 2_097_152)
                throw new InvalidDataException("Unsupported packed NAND integrity record.");
            if (Digest(nandRecord, "source_listing_sha256") != nandRecord.GetProperty("source_listing_sha256").GetString() ||
                Digest(nandRecord, "sha256") != nandRecord.GetProperty("sha256").GetString())
                throw new InvalidDataException("Packed NAND integrity digests must use lowercase SHA-256.");
        }
        Require("nand", "nand", !packedNand);
        // The files each board's boot reads (Portable/src/Storage.cpp's boot_route).
        string board = root.GetProperty("board").GetString()!;
        bool secureRom = board is "n45ap" or "m68ap" or "n72ap";
        string strategy = root.TryGetProperty("boot_strategy", out var bootStrategy) ? bootStrategy.GetString() ?? "" : secureRom ? "iboot" : "kboot";
        if (strategy == "kboot" && !secureRom) Require("kboot", "kboot.bin");
        else if (strategy == "iboot" && (secureRom || board == "k48ap"))
        {
            Require("iboot", "iBoot.bin"); Require("nor", "nor.bin");
            if (board is not ("n45ap" or "m68ap")) Require("gid_blobs", "gid-blobs.bin");
        }
        else if (strategy == "bootrom" && board == "n72ap") { Require("nor", "nor.bin"); Require("gid_blobs", "gid-blobs.bin"); }
        else throw new InvalidDataException($"Unsupported boot strategy {strategy} for {board}.");
        var sizes = new SortedDictionary<string, long>(StringComparer.Ordinal);
        var checks = new List<(string Path, string Digest)>();
        var listings = new List<(string Root, List<string> Names, string Digest, long? Pages)>();
        string Asset(string name)
        {
            PreparedArchive.ValidateName(name);
            var path = Path.Combine(directory, name.Replace('/', Path.DirectorySeparatorChar));
            Check(path);
            if (!File.Exists(path)) throw new InvalidDataException("Missing prepared file: " + name);
            sizes[path] = new FileInfo(path).Length;
            return path;
        }
        foreach (var output in outputs.EnumerateObject())
        {
            var record = output.Value;
            if (record.ValueKind == JsonValueKind.Null) continue;  // an output the board leaves out (a 3.x kboot nor.bin)
            string name = record.GetProperty("path").GetString() ?? "";
            PreparedArchive.ValidateName(name);
            if (record.TryGetProperty("sha256", out _)) checks.Add((Asset(name), Digest(record, "sha256")));
            if (record.TryGetProperty("listing_sha256", out _))
            {
                string path = Path.Combine(directory, name.Replace('/', Path.DirectorySeparatorChar));
                Check(path);
                if (!Directory.Exists(path)) throw new InvalidDataException("NAND is not a directory.");
                var names = new List<string>(); var pending = new Stack<string>(); pending.Push(path);
                while (pending.TryPop(out var next))
                    foreach (string child in Directory.EnumerateFileSystemEntries(next))
                    {
                        Check(child);
                        if (Directory.Exists(child)) pending.Push(child);
                        else
                        {
                            string relative = Path.GetRelativePath(path, child).Replace(Path.DirectorySeparatorChar, '/');
                            PreparedArchive.ValidateName(relative);
                            names.Add(relative); Asset(name + "/" + relative);
                            if (names.Count > 1_000_000) throw new InvalidDataException("NAND listing exceeds its supported bound.");
                        }
                    }
                names.Sort(StringComparer.Ordinal);
                listings.Add((path, names, Digest(record, "listing_sha256"), record.TryGetProperty("pages", out var pages) ? pages.GetInt64() : null));
            }
        }
        if (root.TryGetProperty("identity", out var identity) && identity.TryGetProperty("sha256", out _))
            checks.Add((Asset("identity.json"), Digest(identity, "sha256")));
        long total = 0, done = 0;
        foreach (long size in sizes.Values) total = checked(total + size);
        progress?.Invoke(0, total);
        var hashes = new ConcurrentDictionary<string, string>(StringComparer.Ordinal);
        var progressLock = new object();
        try
        {
            // Schedule bounded groups of NAND pages, while still opening and
            // hashing every on-disk file through the supplied security boundary.
            Parallel.ForEach(Partitioner.Create(sizes.Chunk(64), EnumerablePartitionerOptions.NoBuffering),
                new ParallelOptions { MaxDegreeOfParallelism = maxParallelism },
                () => new byte[1024 * 1024], (batch, _, buffer) => {
                    long pending = 0;
                    void Report()
                    {
                        lock (progressLock) { done += pending; progress?.Invoke(done, total); }
                        pending = 0;
                    }
                    foreach (var (path, size) in batch)
                    {
                        using var source = Open(path); using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
                        if (source.Length != size) throw new InvalidDataException("Prepared file changed size: " + path);
                        if (packedNand && path == Path.Combine(directory, "nand"))
                        {
                            VerifyPackedNand(source, packedPages);
                            source.Position = 0;
                        }
                        long count = 0; int n;
                        while ((n = source.Read(buffer, 0, buffer.Length)) != 0)
                        {
                            count = checked(count + n);
                            if (count > size) throw new InvalidDataException("Prepared file grew: " + path);
                            hash.AppendData(buffer, 0, n);
                            pending += n;
                            if (pending >= buffer.Length) Report();
                        }
                        if (count != size) throw new InvalidDataException("Prepared file truncated: " + path);
                        hashes[path] = Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant();
                    }
                    if (pending != 0) Report();
                    return buffer;
                }, _ => { });
        }
        catch (AggregateException error) { ExceptionDispatchInfo.Capture(error.Flatten().InnerExceptions[0]).Throw(); throw; }
        foreach (var check in checks)
            if (hashes[check.Path] != check.Digest) throw new InvalidDataException("Prepared output checksum differs: " + check.Path);
        foreach (var listing in listings)
        {
            using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
            foreach (string name in listing.Names)
                hash.AppendData(Encoding.UTF8.GetBytes(name + " " + hashes[Path.Combine(listing.Root, name.Replace('/', Path.DirectorySeparatorChar))] + "\n"));
            if (Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant() != listing.Digest ||
                listing.Pages is { } pages && pages != listing.Names.Count)
                throw new InvalidDataException("NAND listing checksum or page count differs.");
        }
        progress?.Invoke(done, total);
    }

    internal static void VerifyPackedNand(Stream source, long pages)
    {
        const int slots = 4 * 524288, recordSize = 4096 + 64;
        const long recordsOffset = 24L + slots * 4L;
        if (pages is < 0 or > slots || source.Length != recordsOffset + pages * recordSize)
            throw new InvalidDataException("Packed NAND is truncated or its record count differs.");
        Span<byte> header = stackalloc byte[24]; source.ReadExactly(header);
        if (!header[..8].SequenceEqual("ITNAND01"u8) ||
            BinaryPrimitives.ReadUInt32LittleEndian(header[8..]) != 4096 ||
            BinaryPrimitives.ReadUInt32LittleEndian(header[12..]) != 64 ||
            BinaryPrimitives.ReadUInt32LittleEndian(header[16..]) != 4 ||
            BinaryPrimitives.ReadUInt32LittleEndian(header[20..]) != 524288)
            throw new InvalidDataException("Unsupported packed NAND geometry.");
        byte[] index = new byte[64 * 1024];
        byte[] seen = new byte[(pages + 7) / 8];
        long referenced = 0;
        for (int remaining = slots * 4; remaining > 0; remaining -= index.Length)
        {
            source.ReadExactly(index);
            for (int offset = 0; offset < index.Length; offset += 4)
            {
                uint record = BinaryPrimitives.ReadUInt32LittleEndian(index.AsSpan(offset, 4));
                if (record > pages)
                    throw new InvalidDataException("Packed NAND index points outside its records.");
                if (record == 0) continue;
                int bit = (int)record - 1;
                if ((seen[bit / 8] & (1 << (bit % 8))) != 0)
                    throw new InvalidDataException("Packed NAND index repeats a record.");
                seen[bit / 8] |= (byte)(1 << (bit % 8));
                ++referenced;
            }
        }
        if (referenced != pages) throw new InvalidDataException("Packed NAND has unreachable records.");
    }
}

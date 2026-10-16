using System.Collections.Concurrent;
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
            root.GetProperty("board").GetString() is not ("n72ap" or "n81ap") ||
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
        Require("nand", "nand", true); Require("nor", "nor.bin");
        if (root.GetProperty("board").GetString() == "n72ap")
        {
            Require("gid_blobs", "gid-blobs.bin");
            if (!root.TryGetProperty("boot_strategy", out var strategy) || strategy.GetString() == "iboot") Require("iboot", "iBoot.bin");
        }
        else Require("kboot", "kboot.bin");
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
            Parallel.ForEach(sizes, new ParallelOptions { MaxDegreeOfParallelism = maxParallelism },
                () => new byte[1024 * 1024], (item, _, buffer) => {
                    var (path, size) = item;
                    using var source = Open(path); using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
                    if (source.Length != size) throw new InvalidDataException("Prepared file changed size: " + path);
                    long count = 0; int n;
                    while ((n = source.Read(buffer, 0, buffer.Length)) != 0)
                    {
                        count = checked(count + n);
                        if (count > size) throw new InvalidDataException("Prepared file grew: " + path);
                        hash.AppendData(buffer, 0, n);
                        lock (progressLock) { done += n; progress?.Invoke(done, total); }
                    }
                    if (count != size) throw new InvalidDataException("Prepared file truncated: " + path);
                    hashes[path] = Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant();
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
}

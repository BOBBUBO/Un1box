using System.Buffers.Binary;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using LightTouchWin.Core;

internal static class ImportTests
{
    private sealed class Inline(Action<double> report) : IProgress<double>
    {
        public void Report(double value) => report(value);
    }

    internal static void Run()
    {
        string root = Path.Combine(Path.GetTempPath(), "lt-import-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root); int passed = 0;
        void Check(bool ok, string name) { if (!ok) throw new Exception(name); ++passed; Console.WriteLine("PASS " + name); }
        void Reject(Action action, string name)
        {
            bool rejected = false;
            try { action(); } catch (Exception e) when (e is IOException or InvalidDataException) { rejected = true; }
            Check(rejected, name);
        }
        string Pack(string name, (string Name, byte[] Bytes)[] entries)
        {
            string path = Path.Combine(root, name);
            using var file = File.Create(path); file.Write("ITPACK01"u8);
            byte[] index = JsonSerializer.SerializeToUtf8Bytes(new { entries = entries.Select(e => new { name = e.Name, size = e.Bytes.Length }) });
            byte[] length = new byte[4]; BinaryPrimitives.WriteUInt32LittleEndian(length, (uint)index.Length);
            file.Write(length); file.Write(index);
            using var zlib = new ZLibStream(file, CompressionLevel.Fastest);
            foreach (var entry in entries) zlib.Write(entry.Bytes);
            return path;
        }
        try
        {
            byte[] large = new byte[3 * 1024 * 1024 + 17]; new Random(42).NextBytes(large);
            var entries = Enumerable.Range(0, 257).Select(i => ($"nand/cs{i % 4}/{i}.page", Encoding.UTF8.GetBytes("page-" + i))).ToList();
            entries.Add(("device.lock.json", "{}"u8.ToArray())); entries.Add(("empty", [])); entries.Add(("large", large));
            string archive = Pack("valid.itbase", entries.ToArray());
            foreach (int workers in new[] { 1, 4 })
            {
                string destination = Path.Combine(root, "extract-" + workers); double last = 0;
                PreparedArchive.Extract(archive, destination, new Inline(value => {
                    if (value < last || value > 1) throw new Exception("nonmonotonic progress"); last = value;
                }), workers);
                Check(last == 1 && entries.All(e => File.ReadAllBytes(Path.Combine(destination, e.Item1)).SequenceEqual(e.Item2)),
                    $"{workers} writers: small pages, zero-length and chunked large asset are byte-exact");
                Reject(() => PreparedArchive.Extract(archive, destination, maxParallelism: workers), "retain existing destination");
                Check(File.ReadAllBytes(Path.Combine(destination, "large")).SequenceEqual(large), "existing destination unchanged");
                byte[] bad = File.ReadAllBytes(archive); bad[^1] ^= 1;
                string corrupt = Path.Combine(root, "corrupt.itbase"); File.WriteAllBytes(corrupt, bad);
                string failed = Path.Combine(root, "failed-" + workers);
                Reject(() => PreparedArchive.Extract(corrupt, failed, maxParallelism: workers), "reject checksum failure");
                Check(!Directory.Exists(failed), "writers finish before failed stage is removed");
                File.WriteAllBytes(corrupt, bad[..^12]);
                Reject(() => PreparedArchive.Extract(corrupt, failed, maxParallelism: workers), "reject truncated payload");
                Check(!Directory.Exists(failed), "truncated stage removed after writers exit");
            }
            string collision = Pack("collision.itbase", [("device.lock.json", []), ("nand", []), ("nand/child", [1])]);
            Reject(() => PreparedArchive.Extract(collision, Path.Combine(root, "collision")), "reject conflicting file/directory before workers start");

            string admitted = Path.Combine(root, "admitted"); Directory.CreateDirectory(Path.Combine(admitted, "nand"));
            File.WriteAllBytes(Path.Combine(admitted, "nor.bin"), [1]); File.WriteAllBytes(Path.Combine(admitted, "kboot.bin"), [2]);
            string Hash(string relative) => Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(Path.Combine(admitted, relative)))).ToLowerInvariant();
            var pages = Enumerable.Range(0, 48).Select(i => i.ToString("D3") + ".page").ToArray();
            foreach (string page in pages) File.WriteAllText(Path.Combine(admitted, "nand", page), page);
            string listing = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(string.Concat(pages.Select(p => p + " " + Hash("nand/" + p) + "\n"))))).ToLowerInvariant();
            File.WriteAllText(Path.Combine(admitted, "device.lock.json"), JsonSerializer.Serialize(new { format = 1, board = "n81ap", build = "8C148", outputs = new {
                nor = new { path = "nor.bin", sha256 = Hash("nor.bin") }, kboot = new { path = "kboot.bin", sha256 = Hash("kboot.bin") },
                nand = new { path = "nand", listing_sha256 = listing, pages = pages.Length } } }));
            int active = 0, peak = 0;
            Stream Read(string path)
            {
                int count = Interlocked.Increment(ref active);
                int previous;
                do { previous = Volatile.Read(ref peak); } while (count > previous && Interlocked.CompareExchange(ref peak, count, previous) != previous);
                try { Thread.Sleep(3); return File.OpenRead(path); }
                finally { Interlocked.Decrement(ref active); }
            }
            long lastDone = 0, total = 0;
            BaseAdmission.Verify(admitted, (done, size) => {
                if (done < lastDone) throw new Exception("nonmonotonic verification progress"); lastDone = done; total = size;
            }, openRead: Read, maxParallelism: 4);
            Check(peak is > 1 and <= 4 && lastDone == total, "verification overlaps bounded reads and reports monotonic progress");
            File.WriteAllText(Path.Combine(admitted, "nand", pages[0]), "modified");
            Reject(() => BaseAdmission.Verify(admitted, maxParallelism: 4), "parallel verification rejects altered NAND");
            Console.WriteLine($"PASS {passed} import assertions; Linux host execution, no Windows performance claim.");
        }
        finally { Directory.Delete(root, recursive: true); }
    }
}

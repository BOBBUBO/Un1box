using System.Buffers;
using System.Buffers.Binary;
using System.Collections.Concurrent;
using System.IO.Compression;
using System.Runtime.ExceptionServices;
using System.Text.Json;

namespace LightTouchWin.Core;

// FirmwareKit/PreparedBase.swift: ITPACK01 + LE index length + JSON + zlib.
// Stream the payload, validate Windows paths before writing, and publish only a
// fully checked extraction. Firmware stays local; no Mac executable is involved.
public static class PreparedArchive
{
    private sealed record Entry(string Name, long Size);
    private sealed record WritePart(string Path, int Offset, int Count);
    private sealed record WriteBatch(byte[] Buffer, List<WritePart> Parts);
    public static void Extract(string archive, string destination, IProgress<double>? progress = null, int maxParallelism = 4)
    {
        if (maxParallelism is < 1 or > 8) throw new ArgumentOutOfRangeException(nameof(maxParallelism));
        if (Directory.Exists(destination) || File.Exists(destination)) throw new IOException("Import destination already exists.");
        using var source = File.OpenRead(archive);
        Span<byte> header = stackalloc byte[12];
        source.ReadExactly(header);
        if (!header[..8].SequenceEqual("ITPACK01"u8)) throw new InvalidDataException("Not a Light Touch .itbase archive.");
        uint length = BinaryPrimitives.ReadUInt32LittleEndian(header[8..]);
        if (length == 0 || length > 64 * 1024 * 1024 || source.Length < 12L + length + 6)
            throw new InvalidDataException("Invalid prepared-device index length.");
        byte[] index = new byte[length]; source.ReadExactly(index);
        using var json = JsonDocument.Parse(index);
        var entries = new List<Entry>();
        var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        long total = 0;
        foreach (var e in json.RootElement.GetProperty("entries").EnumerateArray())
        {
            string name = e.GetProperty("name").GetString() ?? "";
            ValidateName(name);
            long size = e.GetProperty("size").GetInt64();
            if (size < 0 || size > 64L * 1024 * 1024 * 1024 || !names.Add(name) || entries.Count >= 1_000_000)
                throw new InvalidDataException("Invalid or duplicate archive entry.");
            total = checked(total + size);
            if (total > 64L * 1024 * 1024 * 1024) throw new InvalidDataException("Prepared archive exceeds the 64 GiB import limit.");
            entries.Add(new Entry(name, size));
        }
        if (!names.Contains("device.lock.json")) throw new InvalidDataException("Archive has no device.lock.json.");
        foreach (var e in entries)
        {
            int slash = e.Name.IndexOf('/');
            while (slash >= 0)
            {
                if (names.Contains(e.Name[..slash])) throw new InvalidDataException("A file conflicts with an archive directory.");
                slash = e.Name.IndexOf('/', slash + 1);
            }
        }
        long payload = source.Position;
        source.Position = source.Length - 4;
        Span<byte> trailer = stackalloc byte[4]; source.ReadExactly(trailer);
        uint expectedAdler = BinaryPrimitives.ReadUInt32BigEndian(trailer);
        source.Position = payload;
        Directory.CreateDirectory(destination);
        try
        {
            // Create each shared directory once, rather than probing NTFS for
            // the same parent on every NAND page write.
            var directories = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var e in entries) directories.Add(Path.GetDirectoryName(Path.Combine(destination, e.Name.Replace('/', Path.DirectorySeparatorChar)))!);
            foreach (string directory in directories) Directory.CreateDirectory(directory);
            using var zlib = new ZLibStream(source, CompressionMode.Decompress, leaveOpen: true);
            const int chunkSize = 1024 * 1024;
            uint a = 1, b = 0; long written = 0; int percent = -1;
            void Account(byte[] buffer, int n)
            {
                // 5552-byte batches bound the Adler32 sums without a modulo per byte.
                for (int from = 0; from < n; from += 5552)
                {
                    int end = Math.Min(n, from + 5552);
                    for (int i = from; i < end; i++) { a += buffer[i]; b += a; }
                    a %= 65521; b %= 65521;
                }
                written += n;
                int current = (int)(written * 100 / Math.Max(total, 1));
                if (current != percent) { percent = current; progress?.Report(written / (double)Math.Max(total, 1)); }
            }
            IEnumerable<WriteBatch> Inflate()
            {
                int next = 0;
                while (next < entries.Count)
                {
                    var entry = entries[next];
                    if (entry.Size > chunkSize)
                    {
                        ++next;
                        // Large assets cannot occupy a whole-file RAM buffer.
                        // Stream them while other workers finish earlier batches.
                        byte[] buffer = ArrayPool<byte>.Shared.Rent(chunkSize);
                        try
                        {
                            string path = Path.Combine(destination, entry.Name.Replace('/', Path.DirectorySeparatorChar));
                            using var output = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None, bufferSize: 1);
                            long remaining = entry.Size;
                            while (remaining > 0)
                            {
                                int n = (int)Math.Min(chunkSize, remaining);
                                zlib.ReadExactly(buffer.AsSpan(0, n));
                                output.Write(buffer, 0, n); Account(buffer, n); remaining -= n;
                            }
                        }
                        finally { ArrayPool<byte>.Shared.Return(buffer); }
                    }
                    else
                    {
                        // Read across small-file boundaries in one inflate call.
                        // Bound both bytes and entries (including empty files).
                        var parts = new List<WritePart>();
                        int count = 0;
                        while (next < entries.Count && parts.Count < 128)
                        {
                            var e = entries[next];
                            if (e.Size > chunkSize - count) break;
                            parts.Add(new WritePart(Path.Combine(destination, e.Name.Replace('/', Path.DirectorySeparatorChar)), count, (int)e.Size));
                            count += (int)e.Size; ++next;
                        }
                        byte[] buffer = ArrayPool<byte>.Shared.Rent(Math.Max(1, count));
                        try { zlib.ReadExactly(buffer.AsSpan(0, count)); Account(buffer, count); }
                        catch { ArrayPool<byte>.Shared.Return(buffer); throw; }
                        yield return new WriteBatch(buffer, parts);
                    }
                }
                if (zlib.ReadByte() != -1 || (b << 16 | a) != expectedAdler)
                    throw new InvalidDataException("Prepared-device checksum or payload length is invalid.");
            }
            // One ordered inflater; each active writer owns at most 1 MiB.
            // NoBuffering prevents growing prefetch of pooled batch buffers.
            try
            {
                Parallel.ForEach(Partitioner.Create(Inflate(), EnumerablePartitionerOptions.NoBuffering),
                    new ParallelOptions { MaxDegreeOfParallelism = maxParallelism }, batch => {
                        try
                        {
                            foreach (var part in batch.Parts)
                            {
                                using var output = new FileStream(part.Path, FileMode.CreateNew, FileAccess.Write, FileShare.None, bufferSize: 1);
                                output.Write(batch.Buffer, part.Offset, part.Count);
                            }
                        }
                        finally { ArrayPool<byte>.Shared.Return(batch.Buffer); }
                    });
            }
            catch (AggregateException error) { ExceptionDispatchInfo.Capture(error.Flatten().InnerExceptions[0]).Throw(); throw; }
            progress?.Report(1);
        }
        catch
        {
            Directory.Delete(destination, recursive: true);
            throw;
        }
    }
    public static void ValidateName(string name)
    {
        if (string.IsNullOrEmpty(name) || name.IndexOfAny(['\\', ':', '<', '>', '"', '|', '?', '*', '\0']) >= 0)
            throw new InvalidDataException("Invalid Windows archive path.");
        foreach (var part in name.Split('/'))
        {
            string stem = part.Split('.')[0].TrimEnd(' ').ToUpperInvariant();
            if (part is "" or "." or ".." || part.EndsWith('.') || part.EndsWith(' ') || part.Any(char.IsControl) ||
                stem is "CON" or "PRN" or "AUX" or "NUL" or "CONIN$" or "CONOUT$" ||
                (stem.Length == 4 && (stem.StartsWith("COM") || stem.StartsWith("LPT")) && "0123456789¹²³".Contains(stem[3])))
                throw new InvalidDataException("Unsafe or reserved Windows archive path.");
        }
    }
}

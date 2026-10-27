using System.Buffers.Binary;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace LightTouchWin.Core;

// Same ltpkg1 contract as Portable/src/GuestPackage.cpp. Every boot regenerates
// local offer paths; imported Mac offers and host endpoints are never reused.
public static class GuestPackage
{
    private static void Name(string value)
    {
        PreparedArchive.ValidateName(value);
        if (value.Length >= 1024 || value.Any(char.IsWhiteSpace)) throw new InvalidDataException("Invalid guest package name.");
    }
    public static string Prepare(string pack, Device device, string state)
    {
        using var input = File.OpenRead(pack);
        if (input.Length > 80L * 1024 * 1024) throw new InvalidDataException("Guest package exceeds size limit.");
        Span<byte> header = stackalloc byte[12]; input.ReadExactly(header);
        if (!header[..8].SequenceEqual("ITPACK01"u8)) throw new InvalidDataException("Not an ITPACK01 guest package.");
        uint length = BinaryPrimitives.ReadUInt32LittleEndian(header[8..]);
        if (length == 0 || length > 16 * 1024 * 1024 || input.Length < 12L + length + 6)
            throw new InvalidDataException("Invalid guest package index.");
        byte[] index = new byte[length]; input.ReadExactly(index);
        using var json = JsonDocument.Parse(index);
        var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var entries = new List<(string Name, int Size)>();
        long total = 0;
        foreach (var e in json.RootElement.GetProperty("entries").EnumerateArray())
        {
            string name = e.GetProperty("name").GetString() ?? ""; Name(name);
            long size = e.GetProperty("size").GetInt64();
            if (size < 0 || (total += size) > 64 * 1024 * 1024 || !names.Add(name) || entries.Count >= 10000)
                throw new InvalidDataException("Invalid or oversized guest package entries.");
            entries.Add((name, checked((int)size)));
        }
        long payloadStart = input.Position;
        input.Position = input.Length - 4;
        Span<byte> trailer = stackalloc byte[4]; input.ReadExactly(trailer);
        uint expected = BinaryPrimitives.ReadUInt32BigEndian(trailer);
        input.Position = payloadStart;
        byte[] payload = new byte[total];
        using (var zlib = new ZLibStream(input, CompressionMode.Decompress, leaveOpen: true))
        {
            zlib.ReadExactly(payload);
            if (zlib.ReadByte() != -1) throw new InvalidDataException("Excess guest package payload.");
        }
        uint adlerA = 1, adlerB = 0;
        foreach (byte value in payload) { adlerA = (adlerA + value) % 65521; adlerB = (adlerB + adlerA) % 65521; }
        if ((adlerB << 16 | adlerA) != expected) throw new InvalidDataException("Guest package checksum mismatch.");
        var parts = new Dictionary<string, byte[]>(StringComparer.Ordinal);
        int offset = 0;
        foreach (var e in entries) { parts[e.Name] = payload.AsSpan(offset, e.Size).ToArray(); offset += e.Size; }
        using var lockDoc = JsonDocument.Parse(File.ReadAllText(Path.Combine(device.BasePath, "device.lock.json")));
        var recipe = lockDoc.RootElement;
        string board = recipe.GetProperty("board").GetString()!, build = recipe.GetProperty("build").GetString()!;
        if (build.Length == 0 || build.Any(c => char.IsWhiteSpace(c) || char.IsControl(c))) throw new InvalidDataException("Invalid guest build identifier.");
        string major = new(build.TakeWhile(char.IsAsciiDigit).ToArray());
        JsonElement? selected = null; string prefix = "";
        foreach (var part in parts.Where(p => p.Key.EndsWith("/manifest.json", StringComparison.Ordinal)))
        {
            using var candidate = JsonDocument.Parse(part.Value);
            var m = candidate.RootElement;
            if (m.TryGetProperty("stub", out var stub) && stub.ValueKind == JsonValueKind.True) continue;
            var requires = m.GetProperty("requires");
            if (!requires.GetProperty("boards").EnumerateArray().Any(v => v.GetString() == board) ||
                !requires.GetProperty("builds").EnumerateArray().Any(v => v.GetString() == build || v.GetString() == major + "*")) continue;
            if (selected != null) throw new InvalidDataException("Ambiguous guest package family.");
            selected = m.Clone(); prefix = part.Key[..^13];
        }
        var manifest = selected ?? throw new InvalidDataException($"No guest package family matches {board}-{build}.");
        var range = manifest.GetProperty("requires").GetProperty("host").GetProperty("guest-package");
        if (manifest.GetProperty("format").GetInt32() != 1 || range.GetArrayLength() != 2 || range[0].GetInt32() > 1 || range[1].GetInt32() < 1)
            throw new InvalidDataException("Unsupported guest package protocol.");
        bool gles = false; HashSet<string>? allowed = null;
        if (recipe.TryGetProperty("guest_package", out var record))
        {
            gles = record.TryGetProperty("gles", out var gl) && gl.ValueKind == JsonValueKind.True;
            if (record.TryGetProperty("hooks", out var hooks)) allowed = hooks.EnumerateArray().Select(v => v.GetString()!).ToHashSet(StringComparer.Ordinal);
        }
        var selectedHooks = new Dictionary<string, JsonElement>(StringComparer.Ordinal);
        var dropped = new HashSet<string>(StringComparer.Ordinal);
        foreach (var hook in manifest.GetProperty("hooks").EnumerateArray())
        {
            string file = hook.GetProperty("file").GetString()!, target = hook.GetProperty("target").GetString()!;
            Name(file);
            bool graphics = target is "/System/Library/Frameworks/OpenGLES.framework/OpenGLES" or "/System/Library/Frameworks/OpenGLES.framework/MBXGLEngine.bundle/MBXGLEngine";
            if ((graphics && !gles) || (allowed != null && !allowed.Contains(target))) dropped.Add(file);
            else selectedHooks.Add(file, hook);
        }
        string work = Path.Combine(state, "work"); Directory.CreateDirectory(work);
        string stage = Path.Combine(work, ".offer-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(stage);
        try
        {
            string version = manifest.GetProperty("version").GetString()!;
            if (version.Length == 0 || version.Any(c => char.IsWhiteSpace(c) || char.IsControl(c))) throw new InvalidDataException("Invalid package version.");
            var offer = new StringBuilder($"ltpkg 1\nbuild {build}\nserial {manifest.GetProperty("serial").GetInt64()} {version}\n");
            int token = 0;
            var published = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var jobs = manifest.GetProperty("jobs").EnumerateArray().Select(v => v.GetString()!).ToHashSet(StringComparer.Ordinal);
            foreach (var file in manifest.GetProperty("files").EnumerateArray())
            {
                string name = file.GetProperty("name").GetString()!; Name(name);
                if (!published.Add(name)) throw new InvalidDataException("Duplicate manifest payload.");
                if (dropped.Contains(name)) continue;
                if (!parts.TryGetValue(prefix + name, out var bytes) || bytes.LongLength != file.GetProperty("size").GetInt64())
                    throw new InvalidDataException("Guest payload missing or size differs: " + name);
                string sha = file.GetProperty("sha256").GetString()!;
                if (!string.Equals(Convert.ToHexString(SHA256.HashData(bytes)), sha, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("Guest payload checksum differs: " + name);
                string mode = file.GetProperty("mode").GetString()!;
                if (mode.Length != 3 || mode.Any(c => c is < '0' or > '7')) throw new InvalidDataException("Invalid package file mode.");
                string path = Path.Combine(stage, name.Replace('/', Path.DirectorySeparatorChar));
                Directory.CreateDirectory(Path.GetDirectoryName(path)!);
                using (var output = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None)) { output.Write(bytes); output.Flush(true); }
                if (token >= 64) throw new InvalidDataException("Guest protocol supports at most64 payload files.");
                bool isHook = selectedHooks.TryGetValue(name, out var hook);
                offer.Append($"{(isHook ? "hook" : jobs.Contains(name) ? "job" : "file")} {token++} {name} {mode} {bytes.Length} {sha}");
                if (isHook)
                {
                    string target = hook.GetProperty("target").GetString()!;
                    if (!target.StartsWith('/') || target.Any(char.IsWhiteSpace)) throw new InvalidDataException("Invalid guest hook target.");
                    offer.Append(' ').Append(target);
                    if (hook.TryGetProperty("respring", out var respring) && respring.ValueKind == JsonValueKind.True) offer.Append(" respring");
                }
                offer.Append('\n');
            }
            if (Encoding.UTF8.GetByteCount(offer.ToString()) >= 64 * 1024) throw new InvalidDataException("Guest offer exceeds protocol limit.");
            EmulatorSession.WriteDurable(Path.Combine(stage, "offer"), offer.ToString());
            string destination = Path.Combine(work, "guest-offer");
            if (Directory.Exists(destination)) Directory.Delete(destination, true);
            Directory.Move(stage, destination);
            return destination;
        }
        catch { if (Directory.Exists(stage)) Directory.Delete(stage, true); throw; }
    }
}

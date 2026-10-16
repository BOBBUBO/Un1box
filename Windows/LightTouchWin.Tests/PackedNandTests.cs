using System.Buffers.Binary;
using System.Security.Cryptography;
using System.Text.Json;
using LightTouchWin.Core;

internal static class PackedNandTests
{
    internal static void Run()
    {
        string root = Path.Combine(Path.GetTempPath(), "lt-packed-nand-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        string nand = Path.Combine(root, "nand");
        int passed = 0;
        void Check(bool ok, string label) { if (!ok) throw new Exception(label); ++passed; Console.WriteLine("PASS " + label); }
        void Reject(Action action, string label)
        {
            bool rejected = false;
            try { action(); } catch (Exception error) when (error is IOException or InvalidDataException) { rejected = true; }
            Check(rejected, label);
        }
        byte[] original = new byte[24 + 8 * 1024 * 1024 + 2 * 4160];
        "ITNAND01"u8.CopyTo(original);
        uint[] geometry = [4096, 64, 4, 524288];
        for (int i = 0; i < geometry.Length; ++i) BinaryPrimitives.WriteUInt32LittleEndian(original.AsSpan(8 + i * 4), geometry[i]);
        BinaryPrimitives.WriteUInt32LittleEndian(original.AsSpan(24), 1);
        BinaryPrimitives.WriteUInt32LittleEndian(original.AsSpan(24 + 8 * 1024 * 1024 - 4), 2);
        Dictionary<string, object> WriteLock(string board = "n72ap", int pages = 2, string format = "ITNAND01", bool listing = false)
        {
            string Hash(string name) { using var file = File.OpenRead(Path.Combine(root, name)); return Convert.ToHexString(SHA256.HashData(file)).ToLowerInvariant(); }
            var record = new Dictionary<string, object> { ["path"] = "nand", ["format"] = format, ["pages"] = pages,
                ["sha256"] = Hash("nand"), ["source_listing_sha256"] = new string('a', 64) };
            if (listing) record["listing_sha256"] = new string('b', 64);
            var outputs = new Dictionary<string, object> { ["nand"] = record };
            foreach (var (key, name) in new[] { ("nor", "nor.bin"), ("iboot", "iBoot.bin"), ("gid_blobs", "gid-blobs.bin"), ("kboot", "kboot.bin") })
                outputs[key] = new { path = name, sha256 = Hash(name) };
            File.WriteAllText(Path.Combine(root, "device.lock.json"), JsonSerializer.Serialize(new { format = 1, board, build = "synthetic", outputs }));
            return record;
        }
        try
        {
            foreach (string name in new[] { "nor.bin", "iBoot.bin", "gid-blobs.bin", "kboot.bin", "rom" }) File.WriteAllBytes(Path.Combine(root, name), [1, 2, 3]);
            File.WriteAllBytes(nand, original); WriteLock();
            var checkedPaths = new HashSet<string>();
            BaseAdmission.Verify(root, checkPath: path => checkedPaths.Add(path), openRead: path => {
                if (!checkedPaths.Contains(path)) throw new Exception("Packed read bypassed authority"); return File.OpenRead(path);
            });
            Check(true, "admit packed n72 NAND with holes and both index edges through path authority");
            var device = new Device("packed-test", "packed", root, Path.Combine(root, "rom"));
            string state = Path.Combine(root, "state");
            var recipe = BootRecipe.Create(device, state, validateOnly: true);
            Check(recipe[1].Contains(",nand=" + BootRecipe.Escape(nand)) && recipe[1].Contains(",nandrw="), "packed file reaches n72 recipe with separate writable overlay");
            Check(!Directory.Exists(state), "packed recipe validation leaves state untouched");
            WriteLock(board: "n81ap");
            Reject(() => BaseAdmission.Verify(root), "n81 rejects n72 packed geometry");
            Reject(() => BootRecipe.Create(device, state, validateOnly: true), "n81 recipe rejects packed NAND file");
            WriteLock(listing: true); Reject(() => BaseAdmission.Verify(root), "packed record cannot retain directory listing integrity");
            WriteLock(pages: 1); Reject(() => BaseAdmission.Verify(root), "packed record count must match exact file size");
            WriteLock(pages: -1); Reject(() => BaseAdmission.Verify(root), "packed count cannot be negative");
            File.WriteAllBytes(nand, original[..^1]); WriteLock();
            Reject(() => BaseAdmission.Verify(root), "packed records cannot be truncated even with matching SHA256");
            byte[] invalid = (byte[])original.Clone(); BinaryPrimitives.WriteUInt32LittleEndian(invalid.AsSpan(16), 8);
            File.WriteAllBytes(nand, invalid); WriteLock();
            Reject(() => BaseAdmission.Verify(root), "packed chip geometry is bounded");
            invalid = (byte[])original.Clone(); BinaryPrimitives.WriteUInt32LittleEndian(invalid.AsSpan(24), 3);
            File.WriteAllBytes(nand, invalid); WriteLock();
            Reject(() => BaseAdmission.Verify(root), "packed index cannot reference a nonexistent record");
            invalid = (byte[])original.Clone(); BinaryPrimitives.WriteUInt32LittleEndian(invalid.AsSpan(28), 1);
            File.WriteAllBytes(nand, invalid); WriteLock();
            Reject(() => BaseAdmission.Verify(root), "packed index cannot reuse a record ID");
            invalid = (byte[])original.Clone(); BinaryPrimitives.WriteUInt32LittleEndian(invalid.AsSpan(24), 0);
            File.WriteAllBytes(nand, invalid); WriteLock();
            Reject(() => BaseAdmission.Verify(root), "every packed record must be reachable from the index");
            File.WriteAllBytes(nand, original); WriteLock();
            using (var file = new FileStream(nand, FileMode.Open, FileAccess.Write)) { file.Position = original.Length - 1; file.WriteByte(1); }
            Reject(() => BaseAdmission.Verify(root), "packed data checksum is authoritative after geometry checks");
            File.WriteAllBytes(nand, original); WriteLock(format: "unknown");
            Reject(() => BootRecipe.Create(device, state, validateOnly: true), "regular NAND requires the declared packed format");
            Console.WriteLine($"PASS {passed} packed NAND assertions; synthetic data only.");
        }
        finally { Directory.Delete(root, recursive: true); }
    }
}

using System.Buffers.Binary;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using LightTouchWin.Core;

// Actual separate-process QMP fixture, no firmware or emulator runtime claim.
if (args.Contains("-qmp"))
{
    Console.WriteLine("{\"QMP\":{\"version\":{},\"capabilities\":[]}}");
    int shutdowns = 0;
    while (Console.ReadLine() is { } line)
    {
        using var json = JsonDocument.Parse(line);
        var root = json.RootElement;
        string command = root.GetProperty("execute").GetString()!;
        int id = root.GetProperty("id").GetInt32();
        if (command == "system_powerdown")
        {
            bool reject = Environment.GetEnvironmentVariable("LT_TEST_QMP_MODE") == "reject-first" && shutdowns++ == 0;
            Console.WriteLine(JsonSerializer.Serialize(new { @event = "SHUTDOWN", data = new { guest = true, reason = reject ? "host-qmp-quit" : "guest-shutdown" } }));
        }
        Console.WriteLine(JsonSerializer.Serialize(new { id, @return = command == "query-status" ? (object)new { running = false, status = "paused" } : new { } }));
        if (command == "quit") return;
    }
    return;
}

int passed = 0;
void Check(bool ok, string text) { if (!ok) throw new Exception(text); passed++; Console.WriteLine("PASS " + text); }
void Reject(Action action, string text) { bool rejected = false; try { action(); } catch (Exception e) when (e is IOException or InvalidDataException or ArgumentException) { rejected = true; } Check(rejected, text); }
string rootPath = Path.Combine(Path.GetTempPath(), "lt-windows-contract-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(rootPath);
string MakeArchive(string name, params (string Name, byte[] Data)[] entries)
{
    string path = Path.Combine(rootPath, name);
    using var output = File.Create(path); output.Write("ITPACK01"u8);
    var index = JsonSerializer.SerializeToUtf8Bytes(new { entries = entries.Select(e => new { name = e.Name, size = e.Data.Length, mode = 420 }) });
    byte[] length = new byte[4]; BinaryPrimitives.WriteUInt32LittleEndian(length, (uint)index.Length); output.Write(length); output.Write(index);
    using (var zlib = new ZLibStream(output, CompressionLevel.Optimal, leaveOpen: true)) foreach (var entry in entries) zlib.Write(entry.Data);
    return path;
}
Device Fixture(string board, string build, string suffix)
{
    string path = Path.Combine(rootPath, suffix); Directory.CreateDirectory(Path.Combine(path, "nand"));
    File.WriteAllText(Path.Combine(path, "device.lock.json"), JsonSerializer.Serialize(new { board, build, boot_strategy = board == "n72ap" ? "iboot" : "kboot" }));
    foreach (string file in new[] { "nor.bin", "iBoot.bin", "kboot.bin", "gid-blobs.bin" }) File.WriteAllBytes(Path.Combine(path, file), [0, 1, 2, 3]);
    string rom = Path.Combine(rootPath, "synthetic-rom"); File.WriteAllBytes(rom, [4, 5, 6]);
    return new Device(Guid.NewGuid().ToString("N"), suffix, path, board == "n72ap" ? rom : "");
}
try
{
    var n72 = Fixture("n72ap", "7E18", "base72"); var n81 = Fixture("n81ap", "8C148", "base81");
    string state72 = Path.Combine(rootPath, "state72"), state81 = Path.Combine(rootPath, "state81");
    var recipe = BootRecipe.Create(n72, state72, validateOnly: true);
    Check(recipe[recipe.IndexOf("-display") + 1] == "sdl,gl=off,show-cursor=on", "keep host mouse cursor visible while interacting with guest");
    Check(recipe[1].Contains("bootrom=") && recipe[1].Contains("direct-iboot="), "n72 prepared boot recipe");
    Check(!Directory.Exists(state72), "validation does not mutate writable state");
    var later = BootRecipe.Create(n81, state81);
    Check(later[1].Contains("kboot=") && !later[1].Contains("bootrom="), "n81 prepared kboot without separate ROM");
    Check(later[1].Contains("nand-overlay=") && later[1].Contains("nor-rw="), "n81 writable NAND and NOR isolated");
    Reject(() => BootRecipe.Create(n81 with { RomPath = n72.RomPath }, state81), "reject unrelated n81 ROM");
    Check(File.ReadAllBytes(Path.Combine(n81.BasePath, "nor.bin")).SequenceEqual(new byte[] { 0, 1, 2, 3 }), "base NOR unchanged");
    string other = Path.Combine(rootPath, "other81"); Directory.CreateDirectory(Path.Combine(other, "nand"));
    foreach (string file in new[] { "device.lock.json", "nor.bin", "kboot.bin" }) File.Copy(Path.Combine(n81.BasePath, file), Path.Combine(other, file));
    Reject(() => BootRecipe.Create(n81 with { BasePath = other }, state81), "reject same-metadata different base sharing state");
    Check(BootRecipe.Escape("C:\\a,b") == "C:\\a,,b", "escape QEMU option commas");
    string archive = MakeArchive("valid.itbase", ("device.lock.json", "{}"u8.ToArray()), ("nand/data", new byte[] { 0, 1, 2, 255 }));
    string extracted = Path.Combine(rootPath, "extracted"); PreparedArchive.Extract(archive, extracted);
    Check(File.ReadAllBytes(Path.Combine(extracted, "nand/data")).SequenceEqual(new byte[] { 0, 1, 2, 255 }), "stream prepared archive exactly");
    foreach (string bad in new[] { "../escape", "C:/escape", "NUL.txt", "COM¹.txt", "nand\\bad", "file:stream", "trailing." })
    {
        string broken = MakeArchive("bad.itbase", ("device.lock.json", []), (bad, [1]));
        Reject(() => PreparedArchive.Extract(broken, Path.Combine(rootPath, "badout")), "reject archive path " + bad);
    }
    var corrupt = File.ReadAllBytes(archive); corrupt[^1] ^= 1; string corruptPath = Path.Combine(rootPath, "corrupt.itbase"); File.WriteAllBytes(corruptPath, corrupt);
    Reject(() => PreparedArchive.Extract(corruptPath, Path.Combine(rootPath, "corruptout")), "reject corrupted archive checksum");
    Check(!Directory.Exists(Path.Combine(rootPath, "corruptout")), "remove failed extraction only");
    byte[] tool = [1, 3, 5, 7];
    object Manifest(string board = "n81ap", string sha = "") => new { format = 1, serial = 1, version = "test1", requires = new { boards = new[] { board }, builds = new[] { "8*" }, host = new Dictionary<string, int[]> { ["guest-package"] = [1, 1] } }, hooks = Array.Empty<object>(), jobs = Array.Empty<string>(), files = new[] { new { name = "tool", size = tool.Length, mode = "755", sha256 = sha == "" ? Convert.ToHexString(SHA256.HashData(tool)).ToLowerInvariant() : sha } } };
    string pack = MakeArchive("tools.itpack", ("family/manifest.json", JsonSerializer.SerializeToUtf8Bytes(Manifest())), ("family/tool", tool));
    string offerPath = GuestPackage.Prepare(pack, n81, state81);
    Check(File.ReadAllBytes(Path.Combine(offerPath, "tool")).SequenceEqual(tool), "guest payload hashes verified and extracted");
    Check(File.ReadAllText(Path.Combine(offerPath, "offer")).StartsWith("ltpkg 1\nbuild 8C148\nserial 1 test1\nfile 0 tool 755 4 "), "fresh local ltpkg1 offer regenerated");
    string wrong = MakeArchive("wrong.itpack", ("family/manifest.json", JsonSerializer.SerializeToUtf8Bytes(Manifest("n72ap"))), ("family/tool", tool));
    Reject(() => GuestPackage.Prepare(wrong, n81, state81), "reject mismatched package family");
    string badHash = MakeArchive("hash.itpack", ("family/manifest.json", JsonSerializer.SerializeToUtf8Bytes(Manifest(sha: new string('0', 64)))), ("family/tool", tool));
    Reject(() => GuestPackage.Prepare(badHash, n81, state81), "reject modified guest payload");
    Check(File.Exists(Path.Combine(offerPath, "offer")), "failed offer preserves existing published offer");
    var library = new DeviceLibrary(Path.Combine(rootPath, "library")); var added = library.Add(n81.BasePath, "", pack);
    Check(new DeviceLibrary(library.Root).Devices.Single().GuestPackagePath == pack, "persist selected package with guest profile");
    foreach (string mode in new[] { "clean", "reject-first" })
    {
        Environment.SetEnvironmentVariable("LT_TEST_QMP_MODE", mode);
        string state = Path.Combine(rootPath, "qmp-" + mode);
        await using var session = new EmulatorSession();
        await session.StartAsync(Environment.ProcessPath!, n72, state);
        Check(session.IsRunning, "fresh native QMP worker " + mode);
        Check(File.Exists(Path.Combine(state, "shutdown-unconfirmed.json")), "running state marked unconfirmed " + mode);
        bool disposed = false; try { await session.DisposeAsync(); } catch (InvalidOperationException) { disposed = true; }
        Check(disposed && session.IsRunning, "disposal retains live guest " + mode);
        if (mode == "reject-first")
        {
            bool timed = false; try { await session.StopAsync(TimeSpan.FromMilliseconds(150)); } catch (IOException) { timed = true; }
            Check(timed && session.IsRunning && !session.ShutdownConfirmed, "host quit event cannot qualify guest shutdown");
            bool locked = false; try { using var file = new FileStream(Path.Combine(state, "running.lock"), FileMode.Open, FileAccess.ReadWrite, FileShare.None); } catch (IOException) { locked = true; }
            Check(locked && File.Exists(Path.Combine(state, "shutdown-unconfirmed.json")), "timeout retains exclusive state lock and dirty marker");
        }
        await session.StopAsync(TimeSpan.FromSeconds(2));
        Check(!session.IsRunning && session.ShutdownConfirmed, "confirmed guest shutdown and successful native exit " + mode);
        Check(!File.Exists(Path.Combine(state, "shutdown-unconfirmed.json")), "clean marker only after confirmed native exit " + mode);
        await session.DisposeAsync();
        using var unlocked = new FileStream(Path.Combine(state, "running.lock"), FileMode.Open, FileAccess.ReadWrite, FileShare.None);
        Check(unlocked.CanWrite, "state lock released after native exit " + mode);
    }
    Console.WriteLine($"PASS {passed} focused assertions; host contracts only, no Windows/iOS acceptance claim.");
}
finally { Environment.SetEnvironmentVariable("LT_TEST_QMP_MODE", null); Directory.Delete(rootPath, true); }

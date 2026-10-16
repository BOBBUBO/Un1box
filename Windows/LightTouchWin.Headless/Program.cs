using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json;
using LightTouchWin.Core;
using LightTouchWin.Headless;

if (!OperatingSystem.IsWindows()) { Console.Error.WriteLine("LightTouch: the shared Windows helper requires Windows."); return 3; }
try { return await MainWindows(args); }
catch (Exception failure) { Console.Error.WriteLine("LightTouch: " + failure.Message); return 3; }

[System.Runtime.Versioning.SupportedOSPlatform("windows")]
static async Task<int> MainWindows(string[] arguments)
{
    if (arguments.Length == 0) throw new ArgumentException("Use import or run beneath the shared LightTouch launcher.");
    var options = new Dictionary<string, string>(StringComparer.Ordinal);
    for (int i = 1; i < arguments.Length; i += 2)
        if (i + 1 >= arguments.Length || !arguments[i].StartsWith("--") || !options.TryAdd(arguments[i], arguments[i + 1]))
            throw new ArgumentException("Malformed or duplicate helper option.");
    string Need(string name) => options.TryGetValue(name, out string? value) && value.Length > 0 ? value : throw new ArgumentException("Missing " + name);
    void Progress(string phase, long done, long total) => Console.WriteLine($"import phase={phase} percent={(total > 0 ? done * 100 / total : 0)} done={done} total={total}");
    if (arguments[0] == "import")
    {
        if (options.Keys.Any(k => k is not ("--source" or "--destination" or "--sha256"))) throw new ArgumentException("Unknown import option.");
        string source = PrivateWindows.Full(Need("--source")), destination = PrivateWindows.Full(Need("--destination"));
        string expected = Need("--sha256");
        if (expected.Length != 64 || expected.Any(c => !char.IsAsciiHexDigit(c))) throw new ArgumentException("Import requires its calculated SHA-256.");
        string parent = Path.GetDirectoryName(destination) ?? throw new IOException("Choose an import container.");
        PrivateWindows.Ensure(parent);
        var pins = PrivateWindows.Pin(parent, false);
        try
        {
            if (Directory.Exists(destination) || File.Exists(destination)) throw new IOException("Import destination already exists; existing data was retained.");
            using var input = PrivateWindows.Read(source);
            using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
            byte[] buffer = new byte[1024 * 1024]; long done = 0; int n;
            while ((n = input.Read(buffer, 0, buffer.Length)) != 0) { hash.AppendData(buffer, 0, n); done += n; Progress("hashing", done, input.Length); }
            if (!Convert.ToHexString(hash.GetHashAndReset()).Equals(expected, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("Archive SHA-256 differs; no base was imported.");
            string stage = Path.Combine(parent, ".lighttouch-import-" + Guid.NewGuid().ToString("N"));
            Console.Error.WriteLine("[import-stage] " + stage);
            // Reuse the established streamed archive algorithm. The held source
            // denies mutation/delete; the isolated parent excludes other users.
            PreparedArchive.Extract(source, stage, new InlineProgress(value => Progress("extracting", (long)(value * 100), 100)));
            BaseAdmission.Verify(stage, (a, b) => Progress("verifying", a, b), PrivateWindows.Check, PrivateWindows.Read);
            PrivateWindows.Freeze(stage);
            if (!PrivateWindows.MoveFileEx(stage, destination, 8)) throw new System.ComponentModel.Win32Exception(System.Runtime.InteropServices.Marshal.GetLastWin32Error(), "Base publication failed; isolated import stage retained at " + stage);
            Console.WriteLine("import phase=verifying percent=100 done=1 total=1");
            return 0;
        }
        finally { foreach (var pin in pins) pin.Dispose(); }
    }
    if (arguments[0] != "run" || options.Keys.Any(k => k is not ("--id" or "--name" or "--base" or "--state" or "--package" or "--rom" or "--worker" or "--recover" or "--audio")))
        throw new ArgumentException("Unknown shared helper action/option.");
    string identity = Need("--id");
    if (identity.Length != 32 || identity.Any(c => !char.IsAsciiHexDigit(c))) throw new InvalidDataException("Invalid saved device identity.");
    string state = PrivateWindows.Full(Need("--state")), basePath = PrivateWindows.Full(Need("--base"));
    if (state.StartsWith(basePath.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) ||
        basePath.StartsWith(state.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) || state.Equals(basePath, StringComparison.OrdinalIgnoreCase))
        throw new IOException("Writable state must be separate from the immutable base.");
    PrivateWindows.Ensure(state);
    foreach (string sub in new[] { "overlay", "work", "screenshots" }) PrivateWindows.Ensure(Path.Combine(state, sub));
    var statePins = PrivateWindows.Pin(state, false); var basePins = new List<Microsoft.Win32.SafeHandles.SafeFileHandle>();
    Process? child = null;
    var assetPins = new List<Stream>();
    int stopRequested = 0, frontendFailed = 0;
    async Task Stop()
    {
        Interlocked.Exchange(ref stopRequested, 1);
        Process? current = child;
        if (current is null) return;
        try { if (!current.HasExited) { await current.StandardInput.WriteLineAsync("stop"); await current.StandardInput.FlushAsync(); } }
        catch (IOException) { }
        catch (InvalidOperationException) { }
    }
    _ = Task.Run(async () => {
        try
        {
            while (true)
            {
                string? line = await Console.In.ReadLineAsync();
                if (line == "stop" || line is null) { await Stop(); if (line is null) return; }
            }
        }
        catch (IOException) { await Stop(); }
    });
    try
    {
        basePins = PrivateWindows.Pin(basePath, true);
        bool recover = options.GetValueOrDefault("--recover") == "yes";
        string dirty = Path.Combine(state, "shutdown-unconfirmed.json");
        if (File.Exists(dirty) && !recover) throw new IOException("Guest shutdown is unconfirmed; explicitly select recovery to retain and cold-boot this state.");
        // Hold the SAME lock as both existing preview and native worker while
        // preparing. Release before Start; the native child reacquires before
        // dirtying/writing. A competing start wins the lock or fails closed.
        List<string> engine;
        using (var lease = PrivateWindows.Lock(Path.Combine(state, "running.lock")))
        {
            PrivateWindows.StateTree(state);
            var progressClock = Stopwatch.StartNew();
            BaseAdmission.Verify(basePath, (a, b) => {
                if (Volatile.Read(ref stopRequested) != 0) throw new OperationCanceledException("Start was cancelled before guest boot; state was retained.");
                if (progressClock.ElapsedMilliseconds >= 100 || a == b) { Console.WriteLine($"admission done={a} total={b}"); progressClock.Restart(); }
            }, PrivateWindows.ImmutableAsset, PrivateWindows.Read);
            string package = PrivateWindows.Full(Need("--package"));
            assetPins.Add(PrivateWindows.Read(package));
            string rom = options.GetValueOrDefault("--rom", "");
            if (rom.Length > 0) assetPins.Add(PrivateWindows.Read(rom));
            var device = new Device(identity, Need("--name"), basePath, rom, package);
            engine = BootRecipe.Create(device, state, guestPackage: package);
            int display = engine.IndexOf("-display"); engine[display + 1] = "none";
            if (options.GetValueOrDefault("--audio", "yes") == "no") engine[engine.IndexOf("-audio") + 1] = "driver=none";
            engine.AddRange(["-monitor", "none", "-serial", "file:" + Path.Combine(state, "serial.log")]);
        }
        var start = new ProcessStartInfo(PrivateWindows.Full(Need("--worker"))) { UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true, WorkingDirectory = state };
        foreach (string value in new[] { "--title", Need("--name"), "--state", state, "--screenshots", Path.Combine(state, "screenshots") }) start.ArgumentList.Add(value);
        if (recover) start.ArgumentList.Add("--recover");
        start.ArgumentList.Add("--"); foreach (string value in engine) start.ArgumentList.Add(value);
        start.Environment["SDL_AUDIODRIVER"] = "wasapi"; start.Environment["IT_TVOUT_READY"] = "1";
        foreach (string key in new[] { "LT_TCG_IR_CACHE_ENABLE", "LT_TCG_IR_CACHE", "LTM_QEMU_DYLIB" }) start.Environment.Remove(key);
        if (Volatile.Read(ref stopRequested) != 0) throw new OperationCanceledException("Start was cancelled before guest boot; state was retained.");
        child = Process.Start(start) ?? throw new IOException("Cannot start the maintained Windows guest window.");
        if (Volatile.Read(ref stopRequested) != 0) await Stop();
        string? final = null; bool duplicateFinal = false;
        async Task Output(StreamReader reader, bool primary)
        {
            while (await reader.ReadLineAsync() is { } line)
                if (line.StartsWith("FINAL ") && primary) { if (final is not null) duplicateFinal = true; final = line; }
                else
                    try { Console.WriteLine(line.StartsWith("FINAL ") ? "NATIVE_STDERR " + line : line); }
                    catch (IOException) { Interlocked.Exchange(ref frontendFailed, 1); await Stop(); }

        }
        Task stdout = Output(child.StandardOutput, true), stderr = Output(child.StandardError, false);
        await child.WaitForExitAsync(); await Task.WhenAll(stdout, stderr);
        if (!duplicateFinal && Volatile.Read(ref frontendFailed) == 0 && child.ExitCode == 0 && SharedReceipt.Clean(child.ExitCode, final))
        { Console.WriteLine(final); return 0; }
        Console.WriteLine("FINAL status_available=0 state=0 confirmed=0 dirty=1 worker_reaped=1 worker_exit=" + child.ExitCode + " worker_disposal_requested=0 frontend_return=3");
        return 3;
    }
    finally
    {
        // No process disposal while alive, even on a frontend/logging failure.
        if (child is not null && !child.HasExited)
        {
            try { await child.StandardInput.WriteLineAsync("stop"); await child.StandardInput.FlushAsync(); } catch (IOException) { }
            await child.WaitForExitAsync();
        }
        child?.Dispose(); foreach (var pin in assetPins) pin.Dispose(); foreach (var pin in basePins) pin.Dispose(); foreach (var pin in statePins) pin.Dispose();
    }
}

internal sealed class InlineProgress(Action<double> report) : IProgress<double>
{
    public void Report(double value) => report(value);
}

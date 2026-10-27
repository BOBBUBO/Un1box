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
    string? validationCache = options.TryGetValue("--validation-cache", out var cachePath) ? PrivateWindows.Full(cachePath) : null;
    bool ReuseValidation(string directory)
    {
        if (validationCache is null || !Directory.Exists(validationCache)) return false;
        PrivateWindows.Ensure(validationCache);
        var pins = PrivateWindows.Pin(validationCache, false);
        try
        {
            PrivateWindows.ImmutableAsset(Path.Combine(directory, "device.lock.json"));
            return BaseValidationCache.Matches(directory, validationCache, PrivateWindows.DirectoryIdentity(directory), PrivateWindows.Read);
        }
        finally { foreach (var pin in pins) pin.Dispose(); }
    }
    void RememberValidation(string directory)
    {
        if (validationCache is null) return;
        try
        {
            PrivateWindows.Ensure(validationCache);
            var pins = PrivateWindows.Pin(validationCache, false);
            try
            {
                BaseValidationCache.Remember(directory, validationCache, PrivateWindows.DirectoryIdentity(directory), PrivateWindows.Read,
                    (from, to) => {
                        if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException();
                        if (!PrivateWindows.MoveFileEx(from, to, 1 | 8))
                            throw new System.ComponentModel.Win32Exception(System.Runtime.InteropServices.Marshal.GetLastWin32Error(), "Cannot save validation result.");
                    });
            }
            finally { foreach (var pin in pins) pin.Dispose(); }
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or System.ComponentModel.Win32Exception)
        { Console.Error.WriteLine("[validation-cache] Not saved; next Start will verify again: " + error.Message); }
    }
    var importProgressClock = Stopwatch.StartNew(); string lastPhase = "";
    void Progress(string phase, long done, long total)
    {
        if (phase != lastPhase || done == total || importProgressClock.ElapsedMilliseconds >= 100)
        {
            Console.WriteLine($"import phase={phase} percent={(total > 0 ? done * 100 / total : 0)} done={done} total={total}");
            lastPhase = phase; importProgressClock.Restart();
        }
    }
    if (arguments[0] == "finalize-import")
    {
        if (options.Keys.Any(k => k is not ("--stage" or "--destination" or "--validation-cache")))
            throw new ArgumentException("Unknown finalize-import option.");
        string stage = PrivateWindows.Full(Need("--stage")), destination = PrivateWindows.Full(Need("--destination"));
        string parent = Path.GetDirectoryName(destination) ?? throw new IOException("Choose an import container.");
        if (!string.Equals(Path.GetDirectoryName(stage), parent, StringComparison.OrdinalIgnoreCase) ||
            string.Equals(stage, destination, StringComparison.OrdinalIgnoreCase))
            throw new IOException("Import stage and destination must be distinct siblings.");
        PrivateWindows.Ensure(parent);
        var pins = PrivateWindows.Pin(parent, false);
        using var cancellation = new CancellationTokenSource();
        _ = Task.Run(async () => {
            try
            {
                while (true)
                {
                    string? line = await Console.In.ReadLineAsync();
                    if (line is null or "stop") { cancellation.Cancel(); return; }
                }
            }
            catch (IOException) { cancellation.Cancel(); }
        });
        bool protecting = false, published = false;
        try
        {
            if (Directory.Exists(destination) || File.Exists(destination))
                throw new IOException("Import destination already exists; existing data was retained.");
            if (!Directory.Exists(stage)) throw new IOException("Import stage does not exist.");
            PrivateWindows.Ensure(stage);
            PrivateWindows.StateTree(stage);
            cancellation.Token.ThrowIfCancellationRequested();
            BaseAdmission.Verify(stage, (a, b) => {
                if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException();
                cancellation.Token.ThrowIfCancellationRequested(); Progress("verifying", a, b);
            }, path => {
                if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException();
                cancellation.Token.ThrowIfCancellationRequested(); PrivateWindows.Check(path);
            },
                PrivateWindows.Read, maxParallelism: 4);
            Progress("protecting", 0, 1);
            protecting = true;
            PrivateWindows.Freeze(stage, maxParallelism: 4, cancellation.Token.ThrowIfCancellationRequested);
            Progress("protecting", 1, 1);
            cancellation.Token.ThrowIfCancellationRequested();
            // No replace flag: another completed destination is never overwritten.
            if (!PrivateWindows.MoveFileEx(stage, destination, 8))
                throw new System.ComponentModel.Win32Exception(System.Runtime.InteropServices.Marshal.GetLastWin32Error(),
                    "Base publication failed; caller-owned stage retained at " + stage);
            published = true;
            RememberValidation(destination);
            Progress("publishing", 1, 1);
            return 0;
        }
        finally
        {
            try { if (protecting && !published && Directory.Exists(stage)) PrivateWindows.RestoreImportStage(stage); }
            finally { foreach (var pin in pins) pin.Dispose(); }
        }
    }
    if (arguments[0] == "import")
    {
        if (options.Keys.Any(k => k is not ("--source" or "--destination" or "--sha256" or "--validation-cache"))) throw new ArgumentException("Unknown import option.");
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
            const int importWorkers = 4;
            var phaseClock = Stopwatch.StartNew();
            void Timing(string phase)
            {
                Console.Error.WriteLine(FormattableString.Invariant($"[import-timing] phase={phase} seconds={phaseClock.Elapsed.TotalSeconds:F3} workers={importWorkers}"));
                phaseClock.Restart();
            }
            byte[] buffer = new byte[1024 * 1024]; long done = 0; int n;
            while ((n = input.Read(buffer, 0, buffer.Length)) != 0) { hash.AppendData(buffer, 0, n); done += n; Progress("hashing", done, input.Length); }
            if (!Convert.ToHexString(hash.GetHashAndReset()).Equals(expected, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("Archive SHA-256 differs; no base was imported.");
            Timing("hashing");
            string stage = Path.Combine(parent, ".lighttouch-import-" + Guid.NewGuid().ToString("N"));
            Console.Error.WriteLine("[import-stage] " + stage);
            // Reuse the established streamed archive algorithm. The held source
            // denies mutation/delete; the isolated parent excludes other users.
            PreparedArchive.Extract(source, stage, new InlineProgress(value => {
                if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException("The shared Windows helper requires Windows.");
                Progress("extracting", (long)(value * 100), 100);
            }), maxParallelism: importWorkers);
            Timing("extracting");
            BaseAdmission.Verify(stage, (a, b) => {
                if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException("The shared Windows helper requires Windows.");
                Progress("verifying", a, b);
            }, PrivateWindows.Check, PrivateWindows.Read, maxParallelism: importWorkers);
            Timing("verifying");
            Progress("protecting", 0, 1);
            PrivateWindows.Freeze(stage, maxParallelism: importWorkers);
            Progress("protecting", 1, 1);
            Timing("protecting");
            if (!PrivateWindows.MoveFileEx(stage, destination, 8)) throw new System.ComponentModel.Win32Exception(System.Runtime.InteropServices.Marshal.GetLastWin32Error(), "Base publication failed; isolated import stage retained at " + stage);
            Timing("publishing");
            RememberValidation(destination);
            Console.WriteLine("import phase=verifying percent=100 done=1 total=1");
            return 0;
        }
        finally { foreach (var pin in pins) pin.Dispose(); }
    }
    if (arguments[0] != "run" || options.Keys.Any(k => k is not ("--id" or "--name" or "--base" or "--state" or "--package" or "--rom" or "--worker" or "--recover" or "--audio" or "--validation-cache" or "--recheck" or "--usb-endpoint" or "--usb-pid" or "--usb-created")))
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
    Process? serviceOwner = null;
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
        // Delegate execution is a separate platform-analysis boundary from the
        // guarded entrypoint; retain an explicit runtime guard here as well.
        if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException("The shared Windows helper requires Windows.");
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
            if (options.GetValueOrDefault("--recheck") != "yes" && ReuseValidation(basePath))
                Console.WriteLine("[admission] Reusing validated read-only system; directory identity and system lock match.");
            else
            {
                var progressClock = Stopwatch.StartNew();
                BaseAdmission.Verify(basePath, (a, b) => {
                    if (Volatile.Read(ref stopRequested) != 0) throw new OperationCanceledException("Start was cancelled before guest boot; state was retained.");
                    if (progressClock.ElapsedMilliseconds >= 100 || a == b) { Console.WriteLine($"admission done={a} total={b}"); progressClock.Restart(); }
                }, PrivateWindows.ImmutableAsset, PrivateWindows.Read, maxParallelism: 4);
                RememberValidation(basePath);
            }
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
        // Clear ambient endpoints; only the explicit owned launch below may
        // attach this guest to its per-device foreground USB bridge.
        foreach (string key in start.Environment.Keys.ToArray())
            if (key.Equals("IT_USB_TCP", StringComparison.OrdinalIgnoreCase) ||
                key.Equals("USBMUXD_SOCKET_ADDRESS", StringComparison.OrdinalIgnoreCase) ||
                key.Equals("USBMUXD_QEMU_ADDR", StringComparison.OrdinalIgnoreCase) ||
                key.StartsWith("LTW_USBMUX_", StringComparison.OrdinalIgnoreCase) ||
                key.StartsWith("LTW_SESSION_", StringComparison.OrdinalIgnoreCase)) start.Environment.Remove(key);
        if (options.Keys.Any(k => k is "--usb-endpoint" or "--usb-pid" or "--usb-created"))
        {
            string endpoint = Need("--usb-endpoint");
            if (!endpoint.StartsWith("127.0.0.1:", StringComparison.Ordinal) ||
                !ushort.TryParse(endpoint[10..], out ushort port) || port == 0 || endpoint != "127.0.0.1:" + port ||
                !int.TryParse(Need("--usb-pid"), out int pid) || pid <= 0 ||
                !long.TryParse(Need("--usb-created"), out long created) || created <= 0)
                throw new IOException("Invalid owned guest app-service endpoint.");
            serviceOwner = Process.GetProcessById(pid);
            // Force retention of the native process handle before reading its
            // creation identity. No endpoint probe or system USB fallback.
            _ = serviceOwner.Handle;
            if (serviceOwner.HasExited || serviceOwner.StartTime.ToUniversalTime().ToFileTimeUtc() != created)
                throw new IOException("Guest app-service process changed before launch.");
            start.Environment["IT_USB_TCP"] = endpoint;
            Console.WriteLine("[services] Connecting guest to its owned per-device app bridge.");
        }
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
        Console.Error.WriteLine($"[windows-receipt] worker_exit={child.ExitCode} final_received={final is not null} duplicate_final={duplicateFinal} frontend_failed={Volatile.Read(ref frontendFailed)}");
        if (final is not null) Console.Error.WriteLine("NATIVE_RECEIPT " + final);
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
        child?.Dispose(); serviceOwner?.Dispose(); foreach (var pin in assetPins) pin.Dispose(); foreach (var pin in basePins) pin.Dispose(); foreach (var pin in statePins) pin.Dispose();
    }
}

internal sealed class InlineProgress(Action<double> report) : IProgress<double>
{
    public void Report(double value) => report(value);
}

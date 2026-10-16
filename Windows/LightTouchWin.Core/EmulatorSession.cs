using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text.Json;

namespace LightTouchWin.Core;

// One fresh native process per boot. QMP uses inherited private pipes.
// A live guest is never force-terminated by disposal or a shutdown timeout.
public sealed class EmulatorSession : IAsyncDisposable
{
    private Process? process;
    private FileStream? deviceLock;
    private readonly ConcurrentDictionary<int, TaskCompletionSource<JsonElement>> pending = new();
    private readonly SemaphoreSlim writer = new(1);
    private readonly TaskCompletionSource greeting = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private readonly TaskCompletionSource guestShutdown = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private Task? reader;
    private Task? errors;
    private int nextId;
    private string? dirtyPath;
    public bool IsRunning => process is { HasExited: false };
    public bool ShutdownConfirmed { get; private set; }
    public event Action<string>? Log;
    public event Action? Exited;

    public async Task StartAsync(string executable, Device device, string state, string? guestPackage = null)
    {
        if (process != null) throw new InvalidOperationException("A session can only be started once.");
        if (!File.Exists(executable)) throw new FileNotFoundException("The Windows qemu-ios backend is missing.", executable);
        Directory.CreateDirectory(state);
        deviceLock = new FileStream(Path.Combine(state, "running.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
        try
        {
            var args = BootRecipe.Create(device, state, guestPackage: guestPackage);
            args.AddRange(["-qmp", "stdio", "-monitor", "none", "-serial", $"file:{Path.Combine(state, "serial.log")}"]);
            var info = new ProcessStartInfo(executable) {
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardInput = true,
                RedirectStandardOutput = true, RedirectStandardError = true, WorkingDirectory = state
            };
            foreach (var arg in args) info.ArgumentList.Add(arg);
            info.Environment["IT_TVOUT_READY"] = "1";
            if (OperatingSystem.IsWindows()) info.Environment["SDL_AUDIODRIVER"] = "wasapi";
            dirtyPath = Path.Combine(state, "shutdown-unconfirmed.json");
            WriteDurable(dirtyPath, JsonSerializer.Serialize(new { startedUtc = DateTime.UtcNow, reason = "guest shutdown not confirmed" }));
            process = Process.Start(info) ?? throw new IOException("QEMU could not be started.");
            process.Exited += (_, _) => Exited?.Invoke();
            process.EnableRaisingEvents = true;
            reader = ReadAsync(process);
            errors = ReadErrorsAsync(process, Path.Combine(state, "emulator.log"));
            await greeting.Task.WaitAsync(TimeSpan.FromSeconds(30));
            await CommandAsync("qmp_capabilities");
        }
        catch
        {
            // Retain ownership if a native process is still alive. The UI lets
            // the user retry shutdown; releasing this lock would permit corruption.
            if (!IsRunning) await DisposeAsync();
            throw;
        }
    }
    internal static void WriteDurable(string path, string value)
    {
        using var file = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.Read);
        var bytes = System.Text.Encoding.UTF8.GetBytes(value);
        file.Write(bytes); file.Flush(flushToDisk: true);
    }
    private async Task ReadErrorsAsync(Process p, string path)
    {
        using var file = new StreamWriter(path, append: true) { AutoFlush = true };
        while (await p.StandardError.ReadLineAsync() is { } line)
        {
            await file.WriteLineAsync(line);
            Log?.Invoke(line);
        }
    }
    private async Task ReadAsync(Process p)
    {
        Exception failure = new IOException("The emulator exited.");
        try
        {
            while (await p.StandardOutput.ReadLineAsync() is { } line)
            {
                JsonDocument doc;
                try { doc = JsonDocument.Parse(line); }
                catch (JsonException) { Log?.Invoke(line); continue; }
                using (doc)
                {
                    var root = doc.RootElement;
                    if (root.TryGetProperty("QMP", out _)) greeting.TrySetResult();
                    else if (root.TryGetProperty("id", out var id) && id.TryGetInt32(out var n) && pending.TryRemove(n, out var tcs))
                    {
                        if (root.TryGetProperty("error", out var error)) tcs.TrySetException(new IOException(error.ToString()));
                        else tcs.TrySetResult(root.GetProperty("return").Clone());
                    }
                    else if (root.TryGetProperty("event", out var ev))
                    {
                        if (ev.GetString() == "SHUTDOWN" && root.TryGetProperty("data", out var data) &&
                            data.TryGetProperty("guest", out var guest) && guest.ValueKind == JsonValueKind.True &&
                            data.TryGetProperty("reason", out var reason) && reason.GetString() == "guest-shutdown")
                            guestShutdown.TrySetResult();
                        Log?.Invoke($"QMP: {ev.GetString()}");
                    }
                }
            }
        }
        catch (Exception e) { failure = e; }
        finally
        {
            greeting.TrySetException(failure);
            foreach (var (id, tcs) in pending) if (pending.TryRemove(id, out _)) tcs.TrySetException(failure);
        }
    }
    public async Task<JsonElement> CommandAsync(string command, object? arguments = null)
    {
        if (!IsRunning || process == null) throw new IOException("The emulator is not running.");
        int id = Interlocked.Increment(ref nextId);
        var tcs = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        pending[id] = tcs;
        try
        {
            await writer.WaitAsync();
            try
            {
                var payload = new Dictionary<string, object?> { ["execute"] = command, ["id"] = id };
                if (arguments != null) payload["arguments"] = arguments;
                await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(payload));
                await process.StandardInput.FlushAsync();
            }
            finally { writer.Release(); }
            return await tcs.Task.WaitAsync(TimeSpan.FromSeconds(10));
        }
        finally { pending.TryRemove(id, out _); }
    }
    public Task<JsonElement> KeyAsync(params string[] keys) => CommandAsync("send-key", new {
        keys = keys.Select(k => new { type = "qcode", data = k }).ToArray(), hold_time = 100
    });
    public async Task StopAsync(TimeSpan? timeout = null)
    {
        if (process == null || !IsRunning) return;
        var status = await CommandAsync("query-status");
        if (status.TryGetProperty("running", out var running) && !running.GetBoolean()) await CommandAsync("cont");
        await CommandAsync("system_powerdown");
        Log?.Invoke("Waiting for iOS to confirm shutdown…");
        try { await guestShutdown.Task.WaitAsync(timeout ?? TimeSpan.FromSeconds(120)); }
        catch (TimeoutException) { throw new IOException("iOS shutdown remains unconfirmed. The guest and its save lock remain active; retry Shut down."); }
        await CommandAsync("quit");
        await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(10));
        if (process.ExitCode != 0) throw new IOException("Native exit failed; writable state remains marked unconfirmed.");
        if (reader != null) await reader;
        if (errors != null) await errors;
        ShutdownConfirmed = true;
        if (dirtyPath != null) File.Delete(dirtyPath);
        Log?.Invoke("Guest shutdown confirmed; native process exited successfully.");
    }
    public async ValueTask DisposeAsync()
    {
        if (IsRunning) throw new InvalidOperationException("Shut down iOS before closing the session. A live guest will not be force-terminated.");
        if (reader != null) await reader;
        if (errors != null) await errors;
        process?.Dispose(); process = null;
        deviceLock?.Dispose(); deviceLock = null;
    }
}

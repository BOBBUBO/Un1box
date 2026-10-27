using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace LightTouchWin.Core;

// Receipts live in the application's private data folder, outside immutable
// bases. Only Remember after a complete successful verification. A receipt is
// a saved validation result, not a fresh scan of every NAND page.
public static class BaseValidationCache
{
    private sealed record Receipt(int Version, string DirectoryIdentity, string LockSha256);
    private static string RecordPath(string directory, string cache) => Path.Combine(cache,
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(Path.GetFullPath(directory).TrimEnd('\\', '/').ToUpperInvariant()))) + ".json");

    private static string LockHash(string directory, Func<string, Stream> read)
    {
        using var file = read(Path.Combine(directory, "device.lock.json"));
        if (file.Length > 1024 * 1024) throw new InvalidDataException("Device lock exceeds 1 MiB.");
        return Convert.ToHexString(SHA256.HashData(file));
    }

    public static bool Matches(string directory, string cache, string directoryIdentity, Func<string, Stream>? read = null)
    {
        read ??= File.OpenRead;
        Receipt? receipt;
        try
        {
            using var file = read(RecordPath(directory, cache));
            if (file.Length > 4096) return false;
            receipt = JsonSerializer.Deserialize<Receipt>(file);
        }
        catch (Exception error) when (error is IOException or JsonException) { return false; }
        return receipt is { Version: 1 } && receipt.DirectoryIdentity == directoryIdentity && receipt.LockSha256 == LockHash(directory, read);
    }

    public static void Remember(string directory, string cache, string directoryIdentity,
                                Func<string, Stream>? read = null, Action<string, string>? publish = null)
    {
        var receipt = new Receipt(1, directoryIdentity, LockHash(directory, read ?? File.OpenRead));
        string destination = RecordPath(directory, cache), temporary = destination + ".tmp-" + Guid.NewGuid().ToString("N");
        try
        {
            using (var file = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                JsonSerializer.Serialize(file, receipt); file.Flush(flushToDisk: true);
            }
            if (publish is null) File.Move(temporary, destination, overwrite: true);
            else publish(temporary, destination);
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }
}

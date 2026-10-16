namespace LightTouchWin.Core;

// Same terminal proof as Desktop/lighttouch_desktop/session.strict_clean_exit.
// The headless parent forwards a clean native receipt only after actual reap.
public static class SharedReceipt
{
    public static bool Clean(int exitCode, string? line)
    {
        if (exitCode != 0 || line is null || !line.StartsWith("FINAL ")) return false;
        var fields = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (string token in line[6..].Split(' ', StringSplitOptions.RemoveEmptyEntries))
        {
            string[] pair = token.Split('=');
            if (pair.Length != 2 || !fields.TryAdd(pair[0], pair[1])) return false;
        }
        return new Dictionary<string, string> {
            ["status_available"] = "1", ["state"] = "0", ["confirmed"] = "1", ["dirty"] = "0",
            ["worker_reaped"] = "1", ["worker_exit"] = "0", ["worker_disposal_requested"] = "0", ["frontend_return"] = "0"
        }.All(pair => fields.GetValueOrDefault(pair.Key) == pair.Value);
    }
}

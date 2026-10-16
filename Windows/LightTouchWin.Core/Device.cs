using System.Text.Json;

namespace LightTouchWin.Core;

public sealed record Device(string Id, string Name, string BasePath, string RomPath, string GuestPackagePath = "");
public sealed record Board(string Machine, bool Cellular, bool UsbKeyboard);

public static class Boards
{
    public static readonly IReadOnlyDictionary<string, Board> All = new Dictionary<string, Board>
    {
        ["n72ap"] = new("iPod-Touch", false, false),
        ["n81ap"] = new("iPod-Touch-4G", false, true)
    };
}

public sealed class DeviceLibrary
{
    public string Root { get; }
    public List<Device> Devices { get; private set; } = [];
    public DeviceLibrary(string root)
    {
        Root = Path.GetFullPath(root);
        Directory.CreateDirectory(Root);
        var path = Path.Combine(Root, "library.json");
        if (File.Exists(path)) Devices = JsonSerializer.Deserialize<List<Device>>(File.ReadAllText(path))
            ?? throw new InvalidDataException("The device library is empty or corrupt.");
        if (Devices.Any(d => !Guid.TryParseExact(d.Id, "N", out _)))
            throw new InvalidDataException("Invalid device ID in library.json.");
    }
    public string State(Device device) => Path.Combine(Root, "devices", device.Id);
    public Device Add(string basePath, string romPath, string guestPackagePath = "")
    {
        basePath = Path.GetFullPath(basePath);
        using var doc = JsonDocument.Parse(File.ReadAllText(Path.Combine(basePath, "device.lock.json")));
        var board = doc.RootElement.GetProperty("board").GetString() ?? "";
        if (!Boards.All.ContainsKey(board)) throw new InvalidDataException($"Unsupported board: {board}");
        string version = doc.RootElement.TryGetProperty("product_version", out var v) ? v.GetString() ?? "" : "";
        var device = new Device(Guid.NewGuid().ToString("N"), $"{board} • iOS {version}", basePath, romPath, guestPackagePath);
        // Validate before changing the library or copying the NOR.
        BootRecipe.Create(device, State(device), validateOnly: true);
        Devices.Add(device);
        try { Save(); } catch { Devices.Remove(device); throw; }
        return device;
    }
    public void Save()
    {
        var path = Path.Combine(Root, "library.json");
        var temporary = path + ".tmp";
        EmulatorSession.WriteDurable(temporary, JsonSerializer.Serialize(Devices, new JsonSerializerOptions { WriteIndented = true }));
        File.Move(temporary, path, true);
    }
}

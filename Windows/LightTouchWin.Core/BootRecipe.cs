using System.Text.Json;
using System.Security.Cryptography;

namespace LightTouchWin.Core;

// Mirrors HostRuntime/BootRecipe.swift. Prepared bases are read-only; each instance
// gets a page overlay and a private NOR, never a writable shared base.
public static class BootRecipe
{
    public static string Escape(string value) => value.Replace(",", ",,");
    public static List<string> Create(Device device, string state, bool validateOnly = false, bool wifi = false, string? guestPackage = null)
    {
        using var json = JsonDocument.Parse(File.ReadAllText(Path.Combine(device.BasePath, "device.lock.json")));
        var root = json.RootElement;
        var boardId = root.GetProperty("board").GetString() ?? "";
        if (!Boards.All.TryGetValue(boardId, out var board)) throw new InvalidDataException($"Unknown board: {boardId}");
        var first = boardId is "m68ap" or "n45ap";
        var second = boardId == "n72ap";
        var strategy = root.TryGetProperty("boot_strategy", out var s) ? s.GetString() : first || second ? "iboot" : "kboot";
        if (strategy is not ("iboot" or "kboot" or "bootrom")) throw new InvalidDataException("Invalid boot_strategy.");
        if ((first && strategy != "iboot") || (second && strategy == "kboot") ||
            (!first && !second && boardId != "k48ap" && strategy != "kboot"))
            throw new InvalidDataException($"Unsupported boot strategy {strategy} for {boardId}.");
        string FileAt(string name)
        {
            var p = Path.GetFullPath(Path.Combine(device.BasePath, name));
            if (!File.Exists(p)) throw new FileNotFoundException($"Prepared device is missing {name}.", p);
            return p;
        }
        var nand = Path.GetFullPath(Path.Combine(device.BasePath, "nand"));
        if (!Directory.Exists(nand))
        {
            if (!second || !root.TryGetProperty("outputs", out var outputs) ||
                !outputs.TryGetProperty("nand", out var record) ||
                !record.TryGetProperty("format", out var format) || format.GetString() != "ITNAND01" ||
                record.GetProperty("path").GetString() != "nand" || record.TryGetProperty("listing_sha256", out _) ||
                !record.TryGetProperty("sha256", out var digest) || digest.GetString() is not { Length: 64 } sha ||
                sha.Any(c => !char.IsAsciiHexDigit(c) || char.IsUpper(c)) ||
                !record.TryGetProperty("source_listing_sha256", out var provenance) || provenance.GetString() is not { Length: 64 } listing ||
                listing.Any(c => !char.IsAsciiHexDigit(c) || char.IsUpper(c)) || !File.Exists(nand) ||
                (File.GetAttributes(nand) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidDataException($"Prepared NAND is not a supported directory or n72 packed image: {nand}");
            using var packed = File.OpenRead(nand);
            BaseAdmission.VerifyPackedNand(packed, record.GetProperty("pages").GetInt64());
        }
        var norSource = FileAt("nor.bin");
        var nor = Path.Combine(Path.GetFullPath(state), "nor.bin");
        var overlay = Path.Combine(Path.GetFullPath(state), "overlay");
        var machine = board.Machine;
        var reserved = new HashSet<string>(StringComparer.Ordinal) {
            "bootrom", "iboot", "kboot", "direct-iboot", "direct-llb", "nand", "nandrw", "nand-overlay", "nor", "nor-rw",
            "wifi", "usb-tcp-addr", "guest-package", "gid-blobs", "boot-args", "osk", "baseband", "development-fuses"
        };
        void Option(string key, string value) => machine += $",{key}={Escape(value)}";
        if (wifi) throw new NotSupportedException("Networking is not included in this Windows compile preview.");
        if (guestPackage != null)
        {
            if (!File.Exists(guestPackage)) throw new FileNotFoundException("Guest package not found.", guestPackage);
            // Offer creation follows state identity validation below.
        }
        if (first || second)
        {
            if (!File.Exists(device.RomPath)) throw new FileNotFoundException("Select the matching SecureROM file.", device.RomPath);
            Option("bootrom", Path.GetFullPath(device.RomPath));
            Option(first ? "iboot" : "direct-iboot", strategy == "bootrom" ? "" : FileAt("iBoot.bin"));
            if (second)
            {
                Option("direct-llb", ""); Option("osk", "on"); Option("nor", norSource); Option("nor-rw", nor);
                Option("gid-blobs", FileAt("gid-blobs.bin"));
                Option("boot-args", "amfi_allow_any_signature=1 cs_enforcement_disable=1");
            }
        }
        else
        {
            Option(strategy!, FileAt(strategy == "kboot" ? "kboot.bin" : strategy == "iboot" ? "iBoot.bin" : "SecureROM.bin"));
            Option("nor-rw", nor);
            if (strategy != "kboot") Option("gid-blobs", FileAt("gid-blobs.bin"));
            if (strategy == "bootrom") Option("development-fuses", "off");
        }
        Option("nand", nand); Option(second ? "nandrw" : "nand-overlay", overlay);
        Option("wifi", wifi ? "on" : "off");
        if (board.Cellular && !first) Option("baseband", "on");
        if (root.TryGetProperty("machine", out var options))
        {
            foreach (var p in options.EnumerateObject().OrderBy(p => p.Name, StringComparer.Ordinal))
            {
                if (reserved.Contains(p.Name) || p.Name.Any(c => !char.IsAsciiLetterOrDigit(c) && c != '-'))
                    throw new InvalidDataException($"Unsafe or conflicting machine option: {p.Name}");
                var value = p.Value.ValueKind switch {
                    JsonValueKind.String => p.Value.GetString()!, JsonValueKind.True => "on", JsonValueKind.False => "off",
                    JsonValueKind.Number => p.Value.GetRawText(), _ => throw new InvalidDataException($"Non-scalar option: {p.Name}")
                };
                Option(p.Name, value);
            }
        }
        // HostRuntime gets the die-id from identity.json; do not silently change a device's hardware identity.
        var identityPath = Path.Combine(device.BasePath, "identity.json");
        if (!first && !second && File.Exists(identityPath))
        {
            using var identity = JsonDocument.Parse(File.ReadAllText(identityPath));
            if (identity.RootElement.TryGetProperty("die-id", out var die) && die.ValueKind == JsonValueKind.Array)
                Option("die-id", string.Join(":", die.EnumerateArray().Select(v => v.GetString())));
        }
        if (!second && !string.IsNullOrEmpty(device.RomPath))
            throw new InvalidDataException("n81ap boots prepared kboot.bin; clear the separate SecureROM selection.");
        var args = new List<string> { "-M", machine, "-display", "sdl,gl=off,show-cursor=on", "-audio", "driver=sdl", "-no-shutdown" };
        // Chain TCG jumps across guest pages, as the other ports do (Portable/src/Storage.cpp has the measurements).
        args.AddRange(["-accel", "tcg,cross-page-chain=on,indirect-cache=on"]);
        if (second) args.AddRange(["-m", "128M"]);
        if (first) args.AddRange(["-drive", $"if=pflash,format=raw,file={Escape(nor)}"]);
        if (board.UsbKeyboard) args.AddRange(["-device", "usb-kbd,bus=usb-bus.0,max-power=20"]);
        if (wifi) args.AddRange(["-netdev", "user,id=wifi0,restrict=on"]);
        if (board.Cellular) args.AddRange(["-netdev", "user,id=cell0,restrict=on"]);
        // Network is intentionally isolated until the user elects to enable it.
        if (!validateOnly)
        {
            Directory.CreateDirectory(state);
            // Bind this private state to the exact imported path and recipe; identical
            // lock files in different bases must not silently share writable NAND.
            var identity = Path.GetFullPath(device.BasePath) + "\n" + File.ReadAllText(Path.Combine(device.BasePath, "device.lock.json"));
            if (second) identity += "\n" + Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(device.RomPath)));
            var fingerprint = Convert.ToHexString(SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(identity)));
            var pin = Path.Combine(state, "base.sha256");
            if (File.Exists(pin) && File.ReadAllText(pin) != fingerprint)
                throw new InvalidDataException("This prepared base has changed since this device was started. Import it as a new device to keep existing saves safe.");
            if (!File.Exists(pin))
            {
                EmulatorSession.WriteDurable(pin + ".tmp", fingerprint);
                File.Move(pin + ".tmp", pin);
            }
            Directory.CreateDirectory(overlay);
            if (!File.Exists(nor))
            {
                // CopyFile can propagate an immutable base file's Windows
                // permissions. Create an independent file under the private
                // state directory so it inherits writable state permissions.
                var temporary = nor + ".tmp-" + Guid.NewGuid().ToString("N");
                bool created = false;
                try
                {
                    using (var input = new FileStream(norSource, FileMode.Open, FileAccess.Read, FileShare.Read))
                    using (var output = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                    {
                        created = true;
                        input.CopyTo(output);
                        output.Flush(true);
                    }
                    File.Move(temporary, nor);
                }
                catch
                {
                    if (created) File.Delete(temporary);
                    throw;
                }
            }
        }
        if (!validateOnly && guestPackage != null)
        {
            Option("guest-package", GuestPackage.Prepare(guestPackage, device, state));
            args[1] = machine;
        }
        return args;
    }
}

using LightTouchWin.Core;
using System.Diagnostics;
using System.Text.Json;

namespace LightTouchWin;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        ApplicationConfiguration.Initialize();
        try { Application.Run(new MainWindow()); }
        catch (Exception e) { MessageBox.Show(e.ToString(), "LightTouch Windows preview", MessageBoxButtons.OK, MessageBoxIcon.Error); }
    }
}

internal sealed class MainWindow : Form
{
    private readonly DeviceLibrary library = new(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "LightTouch", "Windows-preview"));
    private readonly ListBox devices = new() { Dock = DockStyle.Fill, DisplayMember = "Name" };
    private readonly TextBox log = new() { Dock = DockStyle.Fill, Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical };
    private readonly Label status = new() { Text = "Stopped", AutoSize = true, Padding = new Padding(8) };
    private readonly List<Button> controls = [];
    private EmulatorSession? session;
    private bool busy;
    private bool closing;
    private bool paused;
    public MainWindow()
    {
        Text = "LightTouch — Windows compile preview"; Size = new Size(1050, 700); MinimumSize = new Size(850, 550);
        var layout = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 4, ColumnCount = 1, Padding = new Padding(12) };
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize)); layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100)); layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.Controls.Add(new Label { Text = "Prepared n72ap / n81ap guests • Experimental Windows build\nSelect an existing prepared folder or import .itbase, then select its matching guest .itpack. n81ap uses prepared kboot.bin. Guest display opens in SDL. Windows guest boot, multitouch, audio and persistence still require qualification.", AutoSize = true, MaximumSize = new Size(980, 0), Padding = new Padding(0, 0, 0, 10) });
        var toolbar = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill };
        Add(toolbar, "Open prepared folder…", ImportFolder);
        Add(toolbar, "Import .itbase…", ImportArchive);
        Add(toolbar, "Start", StartGuest);
        Add(toolbar, "Home", async () => { RequireSession(); await session!.KeyAsync("meta_l", "shift", "h"); });
        Add(toolbar, "Lock / Wake", async () => { RequireSession(); await session!.KeyAsync("meta_l", "l"); });
        Add(toolbar, "Pause / Resume", async () => { RequireSession(); await session!.CommandAsync(paused ? "cont" : "stop"); paused = !paused; status.Text = paused ? "Paused" : "Running"; });
        Add(toolbar, "Shut down", StopGuest);
        Add(toolbar, "Open data folder", () => { Process.Start(new ProcessStartInfo(library.Root) { UseShellExecute = true }); return Task.CompletedTask; });
        layout.Controls.Add(toolbar);
        var split = new SplitContainer { Dock = DockStyle.Fill, SplitterDistance = 285 };
        split.Panel1.Controls.Add(devices); split.Panel2.Controls.Add(log); layout.Controls.Add(split);
        layout.Controls.Add(status); Controls.Add(layout);
        foreach (var d in library.Devices) devices.Items.Add(d);
        if (devices.Items.Count > 0) devices.SelectedIndex = 0;
        FormClosing += HandleFormClosing;
    }
    private void Add(Control parent, string title, Func<Task> action)
    {
        var button = new Button { Text = title, AutoSize = true, Padding = new Padding(4) }; controls.Add(button); parent.Controls.Add(button);
        button.Click += async (_, _) => {
            if (busy) return; busy = true;
            foreach (var b in controls) b.Enabled = false;
            try { await action(); }
            catch (Exception e) { Append(e.ToString()); MessageBox.Show(this, e.Message, Text, MessageBoxButtons.OK, MessageBoxIcon.Error); }
            finally { busy = false; foreach (var b in controls) b.Enabled = true; }
        };
    }
    private void RequireSession() { if (session?.IsRunning != true) throw new InvalidOperationException("Start a guest first."); }
    private void Append(string value)
    {
        if (IsDisposed || Disposing) return;
        if (InvokeRequired) { try { BeginInvoke(() => Append(value)); } catch (InvalidOperationException) { } return; }
        if (log.TextLength > 150000) log.Text = log.Text[^75000..];
        log.AppendText(value + Environment.NewLine);
    }
    private Task ImportFolder()
    {
        if (session?.IsRunning == true) throw new InvalidOperationException("Shut down the active guest first.");
        using var picker = new FolderBrowserDialog { Description = "Prepared folder containing device.lock.json and NAND", UseDescriptionForTitle = true };
        if (picker.ShowDialog(this) == DialogResult.OK) AddDevice(picker.SelectedPath);
        return Task.CompletedTask;
    }
    private async Task ImportArchive()
    {
        if (session?.IsRunning == true) throw new InvalidOperationException("Shut down the active guest first.");
        using var picker = new OpenFileDialog { Title = "Import prepared base", Filter = "Prepared base|*.itbase" };
        if (picker.ShowDialog(this) != DialogResult.OK) return;
        string destination = Path.Combine(library.Root, "bases", Guid.NewGuid().ToString("N"));
        bool added = false;
        try
        {
            var progress = new Progress<double>(p => status.Text = $"Importing {p:P0}");
            await Task.Run(() => PreparedArchive.Extract(picker.FileName, destination, progress));
            added = AddDevice(destination);
        }
        finally { if (!added && Directory.Exists(destination)) Directory.Delete(destination, true); status.Text = "Stopped"; }
    }
    private bool AddDevice(string path)
    {
        using var recipe = JsonDocument.Parse(File.ReadAllText(Path.Combine(path, "device.lock.json")));
        string board = recipe.RootElement.GetProperty("board").GetString() ?? "";
        string rom = "";
        if (board == "n72ap")
        {
            using var picker = new OpenFileDialog { Title = "Select matching n72ap SecureROM", Filter = "Boot input|*.*" };
            if (picker.ShowDialog(this) != DialogResult.OK) return false;
            rom = picker.FileName;
        }
        using var pack = new OpenFileDialog { Title = "Select matching ARMv6/ARMv7 guest tools .itpack", Filter = "Guest tools|*.itpack" };
        if (pack.ShowDialog(this) != DialogResult.OK) return false;
        var device = library.Add(path, rom, pack.FileName);
        devices.Items.Add(device); devices.SelectedItem = device;
        Append("Prepared base selected. Writable NOR and NAND overlay use the separate device data folder."); return true;
    }
    private async Task StartGuest()
    {
        if (session?.IsRunning == true) throw new InvalidOperationException("A guest is already active.");
        if (session != null) await session.DisposeAsync();
        if (devices.SelectedItem is not Device device) throw new InvalidOperationException("Select a prepared guest first.");
        string state = library.State(device);
        if (File.Exists(Path.Combine(state, "shutdown-unconfirmed.json")) &&
            MessageBox.Show(this, "The previous guest exit was not confirmed clean. Reuse its preserved writable state?", Text, MessageBoxButtons.YesNo, MessageBoxIcon.Warning) != DialogResult.Yes) return;
        if (string.IsNullOrEmpty(device.GuestPackagePath)) throw new InvalidOperationException("Import this guest with its matching .itpack first.");
        session = new EmulatorSession(); session.Log += Append;
        session.Exited += () => { if (!IsDisposed && IsHandleCreated) { try { BeginInvoke(() => status.Text = "Native process exited; see shutdown status and logs"); } catch (InvalidOperationException) { } } };
        status.Text = "Starting…";
        await session.StartAsync(Path.Combine(AppContext.BaseDirectory, "runtime", "qemu-system-arm.exe"), device, state, device.GuestPackagePath);
        paused = false; status.Text = "Running — Windows guest acceptance unqualified";
    }
    private async Task StopGuest()
    {
        RequireSession(); status.Text = "Waiting for confirmed guest shutdown…";
        await session!.StopAsync(); await session.DisposeAsync(); session = null; status.Text = "Stopped — shutdown confirmed";
    }
    private async void HandleFormClosing(object? sender, FormClosingEventArgs e)
    {
        if (closing) return;
        if (busy || session?.IsRunning == true)
        {
            e.Cancel = true;
            if (!busy) MessageBox.Show(this, "Use Shut down and wait for confirmation before closing. The guest will stay active if shutdown times out.", Text);
            return;
        }
        if (session == null) return;
        e.Cancel = true;
        try { await session.DisposeAsync(); session = null; closing = true; Close(); }
        catch (Exception error) { Append(error.ToString()); }
    }
}

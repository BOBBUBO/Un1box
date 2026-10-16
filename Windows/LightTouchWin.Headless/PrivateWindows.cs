using Microsoft.Win32.SafeHandles;
using LightTouchWin.Core;
using System.ComponentModel;
using System.Collections.Concurrent;
using System.Runtime.ExceptionServices;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Security.AccessControl;
using System.Security.Principal;

namespace LightTouchWin.Headless;

// Narrow native I/O boundary. No firmware interpretation or product widgets.
[SupportedOSPlatform("windows")]
internal static class PrivateWindows
{
    private static readonly SecurityIdentifier User = WindowsIdentity.GetCurrent().User
        ?? throw new IOException("Cannot identify the current Windows user.");
    private static readonly HashSet<string> Trusted = [User.Value, "S-1-5-18", "S-1-5-32-544"];
    // Python 3.13 mkdir(mode=0700) grants OWNER RIGHTS, not a literal user SID.
    // Call only after checking that the object is owned by the current user.
    private const string OwnerRights = "S-1-3-4";
    private static bool TrustedPrincipal(string sid) => Trusted.Contains(sid) || sid == OwnerRights;
    private static bool OwnerPrincipal(string sid) => sid == User.Value || sid == OwnerRights;
    [StructLayout(LayoutKind.Sequential)] private struct FileInfoNative
    {
        public uint Attributes; public System.Runtime.InteropServices.ComTypes.FILETIME Creation, Access, Write;
        public uint Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
    }
    [DllImport("kernel32", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFile(string name, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32", SetLastError = true)] private static extern bool GetFileInformationByHandle(SafeFileHandle file, out FileInfoNative info);
    [DllImport("kernel32", CharSet = CharSet.Unicode, SetLastError = true)]
    internal static extern bool MoveFileEx(string from, string to, uint flags);

    internal static string Full(string value)
    {
        string path = Path.GetFullPath(value);
        if (value.StartsWith("\\\\") || value.StartsWith("//") || path.StartsWith("\\\\") || path.AsSpan(2).Contains(':'))
            throw new IOException("Use a normal local Windows drive path; device namespaces, network shares and alternate streams are unsupported.");
        if (!Path.IsPathFullyQualified(value)) throw new IOException("Choose an absolute Windows path.");
        string tail = value[3..].Replace('\\', '/').TrimEnd('/');
        if (tail.Length > 0) PreparedArchive.ValidateName(tail);
        return path;
    }
    internal static void Check(string value)
    {
        string path = Full(value);
        for (var current = path; current is not null; current = Path.GetDirectoryName(current))
        {
            if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("Links and junctions are unsupported: " + current);
        }
    }
    internal static string DirectoryIdentity(string value)
    {
        Check(value);
        using var handle = CreateFile(Full(value), 0x80, 3, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
        if (handle.IsInvalid || !GetFileInformationByHandle(handle, out var info) || (info.Attributes & 0x410) != 0x10)
            throw new IOException("Cannot identify prepared system directory: " + value);
        return $"{info.Volume:X8}:{info.IndexHigh:X8}{info.IndexLow:X8}:{info.Creation.dwHighDateTime:X8}{info.Creation.dwLowDateTime:X8}";
    }
    private static void Permissions(DirectoryInfo directory, bool immutable)
    {
        var acl = directory.GetAccessControl(AccessControlSections.Owner | AccessControlSections.Access);
        if (!User.Equals(acl.GetOwner(typeof(SecurityIdentifier))) || !acl.AreAccessRulesProtected)
            throw new IOException("Folder must be private, protected and owned by this user: " + directory.FullName);
        foreach (FileSystemAccessRule rule in acl.GetAccessRules(true, true, typeof(SecurityIdentifier)))
        {
            if (!TrustedPrincipal(rule.IdentityReference.Value)) throw new IOException("Folder grants access outside this user, SYSTEM and Administrators: " + directory.FullName);
            if (immutable && rule.AccessControlType == AccessControlType.Allow && OwnerPrincipal(rule.IdentityReference.Value) &&
                (rule.FileSystemRights & (FileSystemRights.Write | FileSystemRights.DeleteSubdirectoriesAndFiles | FileSystemRights.Delete)) != 0)
                throw new IOException("Prepared base is writable. Import a private immutable copy: " + directory.FullName);
        }
    }
    internal static void ImmutableAsset(string path)
    {
        Check(path);
        if (Directory.Exists(path)) { Permissions(new DirectoryInfo(path), true); return; }
        var acl = new FileInfo(path).GetAccessControl(AccessControlSections.Owner | AccessControlSections.Access);
        if (!User.Equals(acl.GetOwner(typeof(SecurityIdentifier))) || !acl.AreAccessRulesProtected)
            throw new IOException("Prepared asset requires protected, owned read-only permissions: " + path);
        foreach (FileSystemAccessRule rule in acl.GetAccessRules(true, true, typeof(SecurityIdentifier)))
        {
            if (!TrustedPrincipal(rule.IdentityReference.Value)) throw new IOException("Prepared asset grants external access: " + path);
            if (rule.AccessControlType == AccessControlType.Allow && OwnerPrincipal(rule.IdentityReference.Value) &&
                (rule.FileSystemRights & (FileSystemRights.Write | FileSystemRights.Delete)) != 0)
                throw new IOException("Prepared asset is writable: " + path);
        }
    }
    private static DirectorySecurity Acl(bool immutable)
    {
        var security = new DirectorySecurity(); security.SetOwner(User); security.SetAccessRuleProtection(true, false);
        foreach (string sid in Trusted)
            security.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(sid),
                immutable && sid == User.Value ? FileSystemRights.ReadAndExecute : FileSystemRights.FullControl,
                InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
        return security;
    }
    internal static void Ensure(string value)
    {
        var path = Full(value); string? parent = Path.GetDirectoryName(path);
        if (!Directory.Exists(path))
        {
            if (parent is not null && !Directory.Exists(parent)) Ensure(parent);
            if (parent is not null) Check(parent);
            new DirectoryInfo(path).Create(Acl(false));
        }
        Check(path); Permissions(new DirectoryInfo(path), false);
    }
    internal static List<SafeFileHandle> Pin(string value, bool immutable)
    {
        var pins = new List<SafeFileHandle>();
        try
        {
            string path = Full(value); Check(path);
            // Ancestors are held without FILE_SHARE_DELETE, including the root.
            for (string? current = path; current is not null; current = Path.GetDirectoryName(current))
            {
                var handle = CreateFile(current, 0x80, 3, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
                if (handle.IsInvalid) { handle.Dispose(); throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot pin " + current); }
                if (!GetFileInformationByHandle(handle, out var info) || (info.Attributes & 0x410) != 0x10)
                { handle.Dispose(); throw new IOException("Expected a directory without reparse points: " + current); }
                pins.Add(handle);
            }
            Permissions(new DirectoryInfo(path), immutable);
            return pins;
        }
        catch { foreach (var pin in pins) pin.Dispose(); throw; }
    }
    internal static FileStream Lock(string path)
    {
        string parent = Path.GetDirectoryName(Full(path)) ?? throw new IOException("Missing lock container.");
        Ensure(parent);
        var handle = CreateFile(Full(path), 0xC0000000, 0, IntPtr.Zero, 4, 0x00200000, IntPtr.Zero);
        if (handle.IsInvalid) { handle.Dispose(); throw new Win32Exception(Marshal.GetLastWin32Error(), "This saved device is in use, or its lease cannot be opened."); }
        if (!GetFileInformationByHandle(handle, out var info) || (info.Attributes & 0x410) != 0 || info.Links != 1)
        { handle.Dispose(); throw new IOException("State lease must be an independent regular file."); }
        return new FileStream(handle, FileAccess.ReadWrite);
    }
    internal static void StateTree(string path)
    {
        Check(path);
        foreach (string child in Directory.EnumerateFileSystemEntries(path))
        {
            Check(child);
            FileSystemSecurity acl = Directory.Exists(child)
                ? new DirectoryInfo(child).GetAccessControl(AccessControlSections.Owner | AccessControlSections.Access)
                : new FileInfo(child).GetAccessControl(AccessControlSections.Owner | AccessControlSections.Access);
            if (!User.Equals(acl.GetOwner(typeof(SecurityIdentifier)))) throw new IOException("State ownership differs: " + child);
            foreach (FileSystemAccessRule rule in acl.GetAccessRules(true, true, typeof(SecurityIdentifier)))
                if (!TrustedPrincipal(rule.IdentityReference.Value)) throw new IOException("State permissions grant external access: " + child);
            if (Directory.Exists(child)) StateTree(child);
            else if (Path.GetFileName(child) != "running.lock") { using var file = Read(child); }
        }
    }
    internal static Stream Read(string path)
    {
        Check(path);
        var handle = CreateFile(Full(path), 0x80000000, 1, IntPtr.Zero, 3, 0x00200000, IntPtr.Zero);
        if (handle.IsInvalid) { handle.Dispose(); throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot read " + path); }
        if (!GetFileInformationByHandle(handle, out var info) || (info.Attributes & 0x410) != 0 || info.Links != 1)
        { handle.Dispose(); throw new IOException("Asset must be a regular independent file: " + path); }
        return new FileStream(handle, FileAccess.Read);
    }
    internal static void Freeze(string value, int maxParallelism = 4)
    {
        if (maxParallelism is < 1 or > 8) throw new ArgumentOutOfRangeException(nameof(maxParallelism));
        Check(value);
        var directories = new List<string>();
        IEnumerable<string> Files()
        {
            var pending = new Stack<string>(); pending.Push(value);
            while (pending.TryPop(out var directory))
            {
                Check(directory); directories.Add(directory);
                foreach (string path in Directory.EnumerateFileSystemEntries(directory))
                {
                    if (Directory.Exists(path)) { Check(path); pending.Push(path); }
                    else yield return path;
                }
            }
        }
        try
        {
            // Amortize scheduling; each file still receives its own protected DACL.
            Parallel.ForEach(Partitioner.Create(Files().Chunk(64), EnumerablePartitionerOptions.NoBuffering),
                new ParallelOptions { MaxDegreeOfParallelism = maxParallelism }, batch => {
                    foreach (string path in batch)
                    {
                        Check(path);
                        // Protected read-only file DACL, not merely decorative READONLY.
                        var acl = new FileSecurity(); acl.SetOwner(User); acl.SetAccessRuleProtection(true, false);
                        foreach (string sid in Trusted) acl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(sid),
                            sid == User.Value ? FileSystemRights.ReadAndExecute : FileSystemRights.FullControl, AccessControlType.Allow));
                        File.SetAttributes(path, File.GetAttributes(path) | FileAttributes.ReadOnly);
                        new FileInfo(path).SetAccessControl(acl);
                    }
                });
        }
        catch (AggregateException error) { ExceptionDispatchInfo.Capture(error.Flatten().InnerExceptions[0]).Throw(); throw; }
        // All file writes/ACL updates are complete before restricting parents.
        for (int i = directories.Count - 1; i >= 0; --i)
        {
            File.SetAttributes(directories[i], File.GetAttributes(directories[i]) | FileAttributes.ReadOnly);
            new DirectoryInfo(directories[i]).SetAccessControl(Acl(true));
        }
    }
}

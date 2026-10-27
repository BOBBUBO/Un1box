import Foundation

#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#elseif canImport(Android)
import Android
#endif
#if canImport(CPortability)
import CPortability
#endif

/// A write to `fd` after its reader has gone fails with EPIPE instead of killing the process with SIGPIPE. Darwin
/// sets that per descriptor; elsewhere SIGPIPE is ignored process-wide.
func suppressSIGPIPE(_ fd: Int32) {
    #if canImport(Darwin)
    _ = fcntl(fd, F_SETNOSIGPIPE, 1)
    #elseif !os(Windows)
    _ = signal(SIGPIPE, SIG_IGN)
    #endif
}

#if !canImport(Darwin)
// HostRuntime's stand-ins (Darwin.close, clonefile, copyfile) for every file here, as Darwin is on Apple platforms.
@_exported import HostRuntime
#endif

#if !canImport(Darwin)
/// <sys/stat.h>'s UF_COMPRESSED: an HFS+ catalog record's decmpfs flag, whatever the host.
let UF_COMPRESSED: UInt32 = 0x20
#endif

/// A TCP socket; Glibc types SOCK_STREAM as an enum.
func tcpSocket() -> Int32 {
    #if canImport(Glibc)
    socket(AF_INET, Int32(SOCK_STREAM.rawValue), 0)
    #else
    socket(AF_INET, SOCK_STREAM, 0)
    #endif
}

extension sockaddr_in {
    /// 127.0.0.1:`port` (0: the kernel's pick at bind); only BSD has sin_len.
    static func loopback(port: UInt16) -> sockaddr_in {
        var a = sockaddr_in()
        #if canImport(Darwin)
        a.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        #endif
        a.sin_family = sa_family_t(AF_INET)
        a.sin_port = port.bigEndian
        a.sin_addr = in_addr(s_addr: inet_addr("127.0.0.1"))
        return a
    }
}

/// Deallocates `length` bytes at `offset` of `fd`, keeping the file's size: F_PUNCHHOLE on Darwin, fallocate's
/// PUNCH_HOLE on Linux and Android. -1 (errno set) where the filesystem cannot.
func punchHole(_ fd: Int32, offset: Int, length: Int) -> Int32 {
    #if canImport(Darwin)
    var hole = fpunchhole_t(fp_flags: 0, reserved: 0, fp_offset: off_t(offset), fp_length: off_t(length))
    return fcntl(fd, F_PUNCHHOLE, &hole)
    #elseif os(Linux) || os(Android)
    return lt_punch_hole(fd, off_t(offset), off_t(length))
    #else
    errno = ENOTSUP
    return -1
    #endif
}

#if os(Android)
/// Bionic's pread/pwrite take non-null buffers; FirmwareKit passes a buffer's baseAddress, nil only when empty.
func pread(_ fd: Int32, _ buffer: UnsafeMutableRawPointer?, _ count: Int, _ offset: off_t) -> Int {
    guard let buffer else { return 0 }
    return Android.pread(fd, buffer, count, offset)
}

func pwrite(_ fd: Int32, _ buffer: UnsafeRawPointer?, _ count: Int, _ offset: off_t) -> Int {
    guard let buffer else { return 0 }
    return Android.pwrite(fd, buffer, count, offset)
}
#endif

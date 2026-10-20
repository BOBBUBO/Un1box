#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#elseif canImport(Android)
import Android
#elseif canImport(ucrt)
import ucrt
#endif
import Foundation
import HostRuntime

/// Best-effort command diagnostics. A lost or blocked consumer must never
/// interrupt owned disk/child cleanup. Normal command delivery is awaited by
/// the CLI after resources have been released; cancellation stops this stream.
public enum FirmwareDiagnostics {
    private static let output = PipeOutput(fileDescriptor: STDERR_FILENO)
    public static func write(_ data: Data) { output.write(data) }
    public static func stop() { output.stop() }
    public static func finish() async -> Bool { await output.finish() }
}

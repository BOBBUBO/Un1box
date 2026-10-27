import Darwin
import Foundation

signal(SIGPIPE, SIG_IGN)
setvbuf(stdout, nil, _IOLBF, 0)

// Asset-free packaging diagnostic. It runs before native session admission,
// engine loading, windows, storage leases or guest/helper creation.
if Array(CommandLine.arguments.dropFirst()) == ["--runtime-info"] {
    let value: [String: Any] = ["format": "lighttouch-native-runtime-info", "version": 1,
        "bundle_url": Bundle.main.bundleURL.path,
        "executable_url": Bundle.main.executableURL?.path ?? "",
        "guest_started": false]
    let data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    try FileHandle.standardOutput.write(contentsOf: data + Data("\n".utf8))
    exit(0)
}

do {
    let arguments = try HostArguments(Array(CommandLine.arguments.dropFirst()))
    if arguments.operation == "run" {
        // NativeSession/DeviceRuntime own one fresh helper and all its cleanup.
        let session = try NativeSession(arguments)
        try session.start()
        withExtendedLifetime(session) { while true { CFRunLoopRun() } }
    } else {
        let task = Task.detached {
            do {
                if arguments.operation == "finalize-import" { try finalizeImport(arguments) }
                else { try importArchive(arguments) }
                exit(0)
            } catch {
                hostLine("LightTouch: prepared-base import failed: \(error)")
                exit(Task.isCancelled ? 143 : 1)
            }
        }
        let input = HostInput { task.cancel() }
        let deadline = Timer(timeInterval: 600, repeats: false) { _ in task.cancel() }
        RunLoop.main.add(deadline, forMode: .common)
        withExtendedLifetime(input) { while true { CFRunLoopRun() } }
    }
} catch {
    hostLine("LightTouch: shared native host refused: \(error)")
    exit(64)
}

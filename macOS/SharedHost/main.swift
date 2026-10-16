import Darwin
import Foundation

signal(SIGPIPE, SIG_IGN)
setvbuf(stdout, nil, _IOLBF, 0)

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
                try importArchive(arguments)
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

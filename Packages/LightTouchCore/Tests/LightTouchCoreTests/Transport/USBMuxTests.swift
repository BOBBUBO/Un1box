import Foundation
import Testing

@testable import LightTouchCore

/// usbmuxd that dies is started again on the sockets the emulator dials, a few times; given up, a new boot starts it.
struct USBMuxTests {
    /// A shared host never adopts a stale PID file and never removes endpoints
    /// owned by its publisher. Joining the task is the process-retirement proof.
    @Test func managedStopJoinsOwnedChildAndPreservesForeignState() async throws {
        try await withScratchDirectory { directory in
            let daemon = directory.appendingPathComponent("usbmuxd")
            try "#!/bin/sh\nexec /bin/sleep 60\n".write(to: daemon, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: daemon.path)
            let paths = managedPaths(directory)
            try FileManager.default.createDirectory(at: paths.work, withIntermediateDirectories: true)
            let unrelated = Process()
            unrelated.executableURL = URL(fileURLWithPath: "/bin/sleep")
            unrelated.arguments = ["60"]
            try unrelated.run()
            defer { unrelated.terminate(); unrelated.waitUntilExit() }
            let stale = "\(unrelated.processIdentifier)\n"
            try stale.write(to: paths.usbmuxPID, atomically: true, encoding: .utf8)
            let client = directory.appendingPathComponent("client")
            let guest = directory.appendingPathComponent("guest")
            for path in [client, guest] {
                try "publisher-owned sentinel".write(to: path, atomically: true, encoding: .utf8)
            }
            let mux = USBMux(binary: daemon.path, restartOnExit: false)
            let endpoint = USBMux.Session(clientSocket: "UNIX:" + client.path, guestAddress: guest.path)
            #expect(mux.startManaged(paths: paths, session: endpoint) != nil)
            await eventually("owned child started") { mux.childIsRunning }
            let pid = try #require(mux.processIdentifier)
            // Legacy synchronous callers must not discard a managed task.
            mux.stop()
            let first = Task { await mux.stopAndWait() }
            let second = Task { await mux.stopAndWait() }
            await first.value; await second.value
            #expect(mux.processIdentifier == nil && !mux.childIsRunning && mux.session == nil)
            #expect(StorageLocations.daemonIdentity(pid) == nil, "the exact child has been reaped")
            #expect(unrelated.isRunning, "a stale PID file grants no signalling authority")
            #expect(try String(contentsOf: paths.usbmuxPID, encoding: .utf8) == stale)
            for path in [client, guest] {
                #expect(try String(contentsOf: path, encoding: .utf8) == "publisher-owned sentinel")
            }
        }
    }

    @Test func managedUnexpectedExitNeverRestarts() async throws {
        try await withScratchDirectory { directory in
            let daemon = directory.appendingPathComponent("usbmuxd")
            let starts = directory.appendingPathComponent("starts")
            try "#!/bin/sh\necho started >> '\(starts.path)'\n".write(to: daemon, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: daemon.path)
            var unavailable = false
            let mux = USBMux(binary: daemon.path, restartOnExit: false) { unavailable = true }
            let endpoint = USBMux.Session(clientSocket: "UNIX:" + directory.appendingPathComponent("c.sock").path,
                                          guestAddress: directory.appendingPathComponent("g.sock").path)
            #expect(mux.startManaged(paths: managedPaths(directory), session: endpoint) != nil)
            await eventually("unexpected child exit revoked session") { unavailable }
            mux.ensureRunning()
            await mux.stopAndWait()
            #expect(try String(contentsOf: starts, encoding: .utf8) == "started\n")
            #expect(mux.session == nil && mux.processIdentifier == nil)
        }
    }

    private func managedPaths(_ directory: URL) -> DeviceInstance.Paths {
        DeviceInstance.Paths(directory: directory, base: directory, overlay: directory, writableNOR: nil,
            snapshot: directory.appendingPathComponent("snapshot"), usbmuxConf: directory.appendingPathComponent("conf"),
            work: directory.appendingPathComponent("work"), logs: directory.appendingPathComponent("logs"))
    }

    /// State audit A-11: an unexpected daemon death left app management off until Force Stop then Start.
    @Test func aDaemonThatDiesStartsAgainOnItsSockets() async throws {
        try await withScratchDirectory { directory in
            let starts = directory.appendingPathComponent("starts")
            let daemon = directory.appendingPathComponent("usbmuxd")
            try "#!/bin/sh\necho \"$USBMUXD_QEMU_ADDR\" >> '\(starts.path)'\nsleep 0.2\n".write(
                to: daemon,
                atomically: true,
                encoding: .utf8
            )
            try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: daemon.path)
            let paths = DeviceInstance.Paths(
                directory: directory,
                base: directory,
                overlay: directory,
                writableNOR: nil,
                snapshot: directory.appendingPathComponent("snapshot"),
                usbmuxConf: directory.appendingPathComponent("conf"),
                work: directory.appendingPathComponent("work"),
                logs: directory.appendingPathComponent("logs")
            )
            func launches() -> [String] {
                ((try? String(contentsOf: starts, encoding: .utf8)) ?? "").split(separator: "\n").map(String.init)
            }
            var gaveUp = false
            let mux = USBMux(binary: daemon.path) { gaveUp = true }
            let session = try #require(mux.start(paths: paths))
            await eventually("started again") { launches().count >= 2 }
            #expect(mux.session?.guestAddress == session.guestAddress, "the same sockets")
            await eventually("given up") { gaveUp }
            #expect(launches().count == 1 + USBMux.restartLimit && mux.session == nil)
            #expect(Set(launches()) == [session.guestAddress])

            mux.ensureRunning()
            #expect(mux.session?.guestAddress == session.guestAddress, "a new boot starts it on the same sockets")
            await eventually("started for the new boot") { launches().count == 2 + USBMux.restartLimit }
            mux.stop()
        }
    }

    /// A crashed app's daemons are reparented to launchd and outlive it; launch reaps every device's orphan (one
    /// lasted until its device happened to start again), never a daemon whose parent is alive.
    @Test func launchReapsOrphanedDaemons() async throws {
        try await withScratchDirectory { state in
            let source = state.appendingPathComponent("daemon.c")
            try "#include <unistd.h>\nint main(void) { sleep(60); return 0; }\n".write(
                to: source,
                atomically: true,
                encoding: .utf8
            )
            let daemon = state.appendingPathComponent("usbmuxd")
            try LibraryFixtures.run("/usr/bin/cc", ["-o", daemon.path, source.path])
            func device() throws -> (DeviceInstance, URL) {
                let id = UUID()
                let instance = DeviceInstance(
                    id: id,
                    name: "device",
                    board: "n72ap",
                    firmware: "ipod-3.1.3",
                    created: .now,
                    base: .init(kind: .prepared, path: "Devices/\(id)/base"),
                    storage: .init(
                        key: id.uuidString,
                        overlay: "Devices/\(id)/overlay",
                        snapshot: "Devices/\(id)/snapshot",
                        usbmuxConf: "Devices/\(id)/usbmuxd-conf"
                    )
                )
                let pidFile = instance.paths(state: state, logs: state).usbmuxPID
                try FileManager.default.createDirectory(
                    at: pidFile.deletingLastPathComponent(),
                    withIntermediateDirectories: true
                )
                return (instance, pidFile)
            }
            // The orphan: started by a shell that exits at once.
            let orphan = try #require(
                pid_t(
                    try LibraryFixtures.run("/bin/sh", ["-c", "\"$0\" >/dev/null 2>&1 & echo $!", daemon.path])
                        .trimmingCharacters(in: .whitespacesAndNewlines)
                )
            )
            await eventually("reparented") { StorageLocations.daemonIdentity(orphan)?.parent == 1 }
            let (crashed, crashedPID) = try device()
            try "\(orphan)\n".write(to: crashedPID, atomically: true, encoding: .utf8)
            // A running app's daemon: its parent (this process) is alive.
            let owned = Process()
            owned.executableURL = daemon
            try owned.run()
            defer { owned.terminate() }
            let (running, runningPID) = try device()
            try "\(owned.processIdentifier)\n".write(to: runningPID, atomically: true, encoding: .utf8)

            #expect(USBMux.reapOrphans([crashed, running], state: state, logs: state) == [orphan])
            await eventually("the orphan exited") { StorageLocations.daemonIdentity(orphan) == nil }
            #expect(owned.isRunning && FileManager.default.fileExists(atPath: runningPID.path))
            #expect(!FileManager.default.fileExists(atPath: crashedPID.path))
        }
    }
}

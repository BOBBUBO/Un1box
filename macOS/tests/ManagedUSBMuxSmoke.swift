// SPDX-License-Identifier: MIT
// Actual LightTouchCore USBMux ownership; no Testing/XCTest, helper or guest.
// Run only under a Root-reviewed native process-tree/RSS/time supervisor.
import Darwin
import Foundation
import LightTouchCore

@main struct ManagedUSBMuxSmoke {
    enum Failure: Error { case check(String) }

    @MainActor static func require(_ value: Bool, _ message: String) throws {
        if !value { throw Failure.check(message) }
    }

    @MainActor static func poll(_ message: String, until condition: () -> Bool) async throws {
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: .seconds(3))
        while !condition() {
            try require(clock.now < deadline, message)
            try await Task.sleep(for: .milliseconds(10))
        }
    }

    @MainActor static func paths(_ root: URL) -> DeviceInstance.Paths {
        let id = UUID()
        let instance = DeviceInstance(id: id, name: "native ownership probe", board: "n72ap",
            firmware: "ipod-3.1.3", created: DeviceInstance.now,
            base: .init(kind: .prepared, path: root.appendingPathComponent("unused-base").path),
            storage: .init(key: id.uuidString, overlay: "unused-overlay", snapshot: "unused-snapshot",
                           usbmuxConf: root.appendingPathComponent("private-conf").path))
        return instance.paths(state: root, logs: root.appendingPathComponent("logs"))
    }

    @MainActor static func ownership(_ root: URL) async throws -> [String: Any] {
        let fm = FileManager.default
        let paths = paths(root)
        try fm.createDirectory(at: paths.work, withIntermediateDirectories: true,
                               attributes: [.posixPermissions: 0o700])
        let daemon = root.appendingPathComponent("owned-daemon")
        try "#!/bin/sh\nexec /bin/sleep 60\n".write(to: daemon, atomically: true, encoding: .utf8)
        try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: daemon.path)
        let companion = Process()
        companion.executableURL = URL(fileURLWithPath: "/bin/sleep")
        companion.arguments = ["60"]
        try companion.run()
        defer {
            if companion.isRunning { companion.terminate() }
            companion.waitUntilExit()
        }
        let stale = "\(companion.processIdentifier)\n"
        try stale.write(to: paths.usbmuxPID, atomically: true, encoding: .utf8)
        let endpoints = [root.appendingPathComponent("publisher-client"), root.appendingPathComponent("publisher-guest")]
        for path in endpoints { try "retain".write(to: path, atomically: true, encoding: .utf8) }
        let endpoint = USBMux.Session(clientSocket: "UNIX:" + endpoints[0].path, guestAddress: endpoints[1].path)
        let mux = USBMux(binary: daemon.path, restartOnExit: false)
        do {
            try require(mux.startManaged(paths: paths, session: endpoint) != nil, "managed start rejected")
            try await poll("owned child did not start") { mux.childIsRunning }
            guard let pid = mux.processIdentifier else { throw Failure.check("missing actual child PID") }
            mux.stop() // Must retain the managed task so awaited callers can join it.
            let first = Task { await mux.stopAndWait() }
            let second = Task { await mux.stopAndWait() }
            await first.value; await second.value
            try require(mux.processIdentifier == nil && !mux.childIsRunning && mux.session == nil,
                        "managed ownership not retired")
            try require(StorageLocations.daemonIdentity(pid) == nil, "exact owned child still present")
            try require(companion.isRunning, "unrelated child was signalled from stale PID file")
            try require(try String(contentsOf: paths.usbmuxPID, encoding: .utf8) == stale, "stale PID file mutated")
            for path in endpoints {
                try require(try String(contentsOf: path, encoding: .utf8) == "retain", "publisher endpoint unlinked")
            }
            return ["status": "PASS", "owned_pid": pid, "owned_pid_absent": true,
                    "stale_pid_child_untouched": true, "publisher_sentinels_unchanged": true]
        } catch {
            await mux.stopAndWait()
            throw error
        }
    }

    @MainActor static func noRestart(_ root: URL) async throws -> [String: Any] {
        let daemon = root.appendingPathComponent("exit-daemon")
        try "#!/bin/sh\nprintf x >> \"$0.starts\"\nexit 0\n".write(to: daemon, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: daemon.path)
        var unavailable = false
        let mux = USBMux(binary: daemon.path, restartOnExit: false) { unavailable = true }
        let endpoint = USBMux.Session(clientSocket: "UNIX:" + root.appendingPathComponent("c.sock").path,
                                      guestAddress: root.appendingPathComponent("g.sock").path)
        do {
            try require(mux.startManaged(paths: paths(root), session: endpoint) != nil, "exit daemon start rejected")
            try await poll("unexpected child exit did not revoke session") { unavailable }
            mux.ensureRunning()
            await mux.stopAndWait()
            try require(try String(contentsOfFile: daemon.path + ".starts", encoding: .utf8) == "x",
                        "managed child restarted")
            try require(mux.session == nil && mux.processIdentifier == nil, "failed child not retired")
            return ["status": "PASS", "starts": 1, "automatic_restart": false]
        } catch {
            await mux.stopAndWait()
            throw error
        }
    }

    @MainActor static func main() async {
        do {
            guard CommandLine.arguments.count == 2 else { throw Failure.check("one new private output root required") }
            let root = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
            try require(root.path.hasPrefix("/") && root.standardizedFileURL == root &&
                root.deletingLastPathComponent().resolvingSymlinksInPath() == root.deletingLastPathComponent(),
                "canonical output root required")
            try require(!FileManager.default.fileExists(atPath: root.path), "output already exists; no retries")
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false,
                                                    attributes: [.posixPermissions: 0o700])
            let owned = try await ownership(root)
            let noRestart = try await noRestart(root)
            let result: [String: Any] = ["status": "PASS", "ownership": owned, "no_restart": noRestart,
                "native_library_executed": true, "swift_testing_used": false,
                "real_daemon_socket_modes_verified": false, "guest_started": false]
            let bytes = try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
            try bytes.write(to: root.appendingPathComponent("RESULT.json"), options: .atomic)
            try FileHandle.standardOutput.write(contentsOf: bytes + Data("\n".utf8))
        } catch {
            hostFailure(error)
            exit(1)
        }
    }

    static func hostFailure(_ error: Error) {
        try? FileHandle.standardError.write(contentsOf: Data("FAIL actual native USBMux ownership: \(error)\n".utf8))
    }
}

import Darwin
import Foundation
import LightTouchCore

/// Versioned client-only receipt consumed by the maintained desktop Apps UI.
/// Publishing this does not create a second usbmux owner in Python.
struct NativeServicesReceipt: Encodable {
    let format = "lighttouch-native-services"
    let version = 1
    let launch_id: String
    let profile_id: String
    let owner_pid: Int32
    let generation: Int
    let state: String
    let client_socket: String?
}

/// One native-owned USBMux for this fresh helper launch. Reuses the upstream
/// transport/daemon arguments and awaited subprocess teardown. No system socket,
/// TCP endpoint, stale PID-file signalling or automatic restart is admitted.
@MainActor final class NativeAppServices {
    private struct Identity: Equatable {
        let device: dev_t
        let inode: ino_t
        init(_ info: stat) { device = info.st_dev; inode = info.st_ino }
    }
    private let launch: String
    private let profile: String
    private let directory: URL
    private let directoryIdentity: Identity
    private let endpoint: USBMux.Session
    private let mux: USBMux
    private let paths: DeviceInstance.Paths
    private var sockets: [String: Identity] = [:]
    private var generation = 0
    private var ready = false
    private var started = false
    private var stopping = false
    private var retired = false
    private var retirement: Task<Bool, Never>?
    private var lastState: String?

    /// Missing explicit bundled inputs leaves services unavailable; it never
    /// searches an installed Cocoa app, developer checkout or system daemon.
    static func make(args: HostArguments, paths: DeviceInstance.Paths) -> NativeAppServices? {
        guard let launch = ProcessInfo.processInfo.environment["LIGHTTOUCH_SHARED_SERVICE_SESSION"],
            validID(launch), let profile = args.values["id"], validID(profile)
        else { return nil }
        guard let binary = args.values["usbmuxd"] else {
            publish(launch: launch, profile: profile, generation: 1, state: "unavailable", socket: nil)
            return nil
        }
        do {
            return try NativeAppServices(launch: launch, profile: profile,
                                         binary: URL(fileURLWithPath: binary), paths: paths)
        } catch {
            // Optional app management never weakens guest admission or turns a
            // missing/bad packaged service input into a successful endpoint.
            publish(launch: launch, profile: profile, generation: 1, state: "unavailable", socket: nil)
            hostLine("LightTouch: explicit app-services inputs unavailable; guest USB management disabled")
            return nil
        }
    }

    private static func validID(_ value: String) -> Bool {
        value.count == 32 && value.allSatisfy { "0123456789abcdef".contains($0) }
    }

    private init(launch: String, profile: String, binary: URL, paths: DeviceInstance.Paths) throws {
        var executable = stat()
        guard binary.resolvingSymlinksInPath().standardizedFileURL.path == binary.path,
            lstat(binary.path, &executable) == 0, executable.st_mode & S_IFMT == S_IFREG,
            FileManager.default.isExecutableFile(atPath: binary.path)
        else { throw HostArguments.Failure.usage("The explicit guest USB bridge must be a canonical executable file.") }
        let parent = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .resolvingSymlinksInPath().standardizedFileURL
        var parentInfo = stat()
        guard lstat(parent.path, &parentInfo) == 0, parentInfo.st_mode & S_IFMT == S_IFDIR,
            parentInfo.st_uid == geteuid(), parentInfo.st_mode & 0o077 == 0
        else { throw HostArguments.Failure.usage("A private canonical native service temporary directory is required.") }
        var template = parent.appendingPathComponent("ltm-svc-XXXXXX").path.utf8CString
        guard template.withUnsafeMutableBufferPointer({ mkdtemp($0.baseAddress!) != nil }) else {
            throw StorageLocations.posixError()
        }
        let directory = URL(fileURLWithPath: template.withUnsafeBufferPointer {
            String(cString: $0.baseAddress!)
        }, isDirectory: true)
        var info = stat()
        guard lstat(directory.path, &info) == 0, info.st_mode & S_IFMT == S_IFDIR,
            info.st_uid == geteuid(), info.st_mode & 0o077 == 0,
            directory.appendingPathComponent("g.sock").path.utf8.count < 104
        else {
            rmdir(directory.path)
            throw HostArguments.Failure.usage("Private native USB socket paths exceed the platform limit.")
        }
        self.launch = launch; self.profile = profile
        self.directory = directory; self.directoryIdentity = Identity(info)
        self.paths = paths
        endpoint = USBMux.Session(clientSocket: "UNIX:" + directory.appendingPathComponent("c.sock").path,
                                  guestAddress: directory.appendingPathComponent("g.sock").path)
        mux = USBMux(binary: binary.path, restartOnExit: false)
        mux.onUnexpectedExit = { [weak self] in self?.unavailable() }
    }

    func start() {
        guard !started, !stopping, !retired else { return }
        started = true
        guard mux.startManaged(paths: paths, session: endpoint) != nil else {
            unavailable(); return
        }
    }

    /// Admission waits for the live owned child and both private socket nodes
    /// before supplying guestAddress. This is endpoint readiness, not guest
    /// pairing, protocol health or successful IPA installation.
    func waitReady() async -> Bool {
        guard started, mux.session != nil else { unavailable(); return false }
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: .seconds(5))
        while !stopping && clock.now < deadline {
            if inspectReady() { return true }
            do { try await Task.sleep(for: .milliseconds(25)); try Task.checkCancellation() }
            catch { unavailable(); return false }
        }
        unavailable(); return false
    }

    var guestAddress: String? { ready && !stopping ? endpoint.guestAddress : nil }

    private func parentMatches() -> Bool {
        var info = stat()
        return lstat(directory.path, &info) == 0 && info.st_mode & S_IFMT == S_IFDIR &&
            info.st_uid == geteuid() && info.st_mode & 0o077 == 0 && Identity(info) == directoryIdentity
    }

    private func socketIdentity(_ path: String) -> Identity? {
        var info = stat()
        guard parentMatches(), lstat(path, &info) == 0, info.st_mode & S_IFMT == S_IFSOCK,
            info.st_uid == geteuid(), info.st_mode & 0o077 == 0 else { return nil }
        return Identity(info)
    }

    private func inspectReady() -> Bool {
        guard !stopping, !retired, mux.childIsRunning, mux.session != nil else { return false }
        var complete = true
        for path in endpoint.paths {
            if let identity = socketIdentity(path), sockets[path] == nil || sockets[path] == identity {
                // Pin partial readiness too: cleanup must never unlink an
                // endpoint whose inode this owner has not actually observed.
                sockets[path] = identity
            } else {
                complete = false
            }
        }
        guard complete else { return false }
        ready = true
        emit("ready", socket: endpoint.clientSocket)
        return true
    }

    func tick() {
        if ready && !inspectReady() { unavailable() }
    }

    /// Revoke the client before ordinary guest Stop. The transport stays owned
    /// until the helper has retired; clients cannot start a fresh operation.
    func willStop() {
        stopping = true; unavailable()
    }

    private func unavailable() {
        ready = false; emit("unavailable")
    }

    /// Failed optional readiness must not hold guest preparation behind daemon
    /// teardown. Retain exactly one cleanup task; FINAL still awaits its result.
    func beginRetirement() {
        guard retirement == nil else { return }
        willStop()
        retirement = Task { @MainActor [self] in await retireOwned() }
    }

    /// Await the upstream subprocess TERM/5s/KILL/reap before a native FINAL.
    /// Unexpected files are retained and prevent a clean-state claim.
    func retire() async -> Bool {
        beginRetirement()
        guard let retirement else { return false }
        return await retirement.value
    }

    private func retireOwned() async -> Bool {
        await mux.stopAndWait()
        retired = true
        var clean = parentMatches()
        if clean {
            for path in endpoint.paths {
                var info = stat()
                if lstat(path, &info) != 0 {
                    if errno != ENOENT { clean = false }
                    continue
                }
                guard let current = socketIdentity(path), sockets[path] == current else {
                    clean = false; continue
                }
                if unlink(path) != 0 { clean = false }
            }
            if clean && rmdir(directory.path) != 0 { clean = false }
        }
        emit(clean ? "retired" : "unavailable")
        return clean
    }

    private func emit(_ state: String, socket: String? = nil) {
        guard lastState != state else { return }
        generation += 1; lastState = state
        Self.publish(launch: launch, profile: profile, generation: generation, state: state, socket: socket)
    }

    private static func publish(launch: String, profile: String, generation: Int,
                                state: String, socket: String?) {
        let value = NativeServicesReceipt(launch_id: launch, profile_id: profile,
            owner_pid: getpid(), generation: generation, state: state, client_socket: socket)
        guard let bytes = try? JSONEncoder().encode(value), bytes.count < 8192,
            let line = String(data: bytes, encoding: .utf8) else { return }
        hostLine("SERVICES " + line)
    }
}

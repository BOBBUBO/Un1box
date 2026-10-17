import CryptoKit
import Darwin
import DeviceRuntime
import Foundation
import HostRuntime
import IOSurface
import LightTouchCore
import LightTouchSharedDesktopMac

/// Native owner for one shared-desktop launch. This reuses native records,
/// admission, recipe, guest-package, DeviceRuntime and log boundaries.
@MainActor final class NativeSession {
    private let args: HostArguments
    private let state: URL
    private let markers: DurableState
    private let dirtyBytes: Data
    private let sessionLease: StorageLease
    private let capture: ProcessLogCapture
    private var instance: DeviceInstance
    private let process: DeviceSessionProcess
    private var window: SharedGuestWindow?
    private var input: HostInput?
    private var timer: Timer?
    private var booted = false
    private var stopping = false
    private var shutdownStarted: Date?
    private var quitAfterConfirmation = false
    private var forced = false
    private var finished = false
    private var admittedStorage: StorageBootProof?
    private let started = Date()
    private var lastStatus = Date.distantPast
    private var termination: DeviceTermination?
    private var qemuExit: Int32?
    private var cleanupOverdue = false
    private var services: NativeAppServices?
    private var finishing = false

    init(_ args: HostArguments) throws {
        self.args = args
        let fm = FileManager.default
        state = args.url("state")
        dirtyBytes = Data("shared-desktop session \(UUID().uuidString) pending confirmed shutdown\n".utf8)
        guard let hex = args.values["id"], hex.count == 32,
            hex.allSatisfy({ "0123456789abcdef".contains($0) }),
            let id = UUID(uuidString: "\(hex.prefix(8))-\(hex.dropFirst(8).prefix(4))-\(hex.dropFirst(12).prefix(4))-\(hex.dropFirst(16).prefix(4))-\(hex.suffix(12))")
        else { throw HostArguments.Failure.usage("Invalid saved device identity.") }
        var rootInfo = stat()
        if lstat(state.path, &rootInfo) == 0, rootInfo.st_mode & S_IFMT != S_IFDIR {
            throw HostArguments.Failure.usage("State must be a regular private directory.")
        }
        let existing = (try? fm.contentsOfDirectory(atPath: state.path)) ?? []
        try fm.createDirectory(at: state, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        // A separate frontend exclusion spans stopped admission -> native lease
        // transfer. The actual helper still takes/rechecks its native lease.
        sessionLease = try StorageLease(state.appendingPathComponent(".shared-session-lease"))
        markers = try DurableState(state)
        let base = args.url("base")
        guard StoragePathAuthority.canonicalPath(base) != StoragePathAuthority.canonicalPath(state),
            !StoragePathAuthority.canonicalPath(base).hasPrefix(StoragePathAuthority.canonicalPath(state) + "/"),
            !StoragePathAuthority.canonicalPath(state).hasPrefix(StoragePathAuthority.canonicalPath(base) + "/")
        else { throw HostArguments.Failure.usage("Base and writable state overlap.") }
        var baseInfo = stat()
        guard lstat(base.path, &baseInfo) == 0, baseInfo.st_mode & S_IFMT == S_IFDIR,
            baseInfo.st_mode & 0o222 == 0 || baseInfo.st_flags & UInt32(UF_IMMUTABLE) != 0,
            let lock = try DeviceLock.read(base: base), let boardID = lock.board,
            ["n72ap", "n81ap"].contains(boardID), let build = lock.build,
            let board = Board(rawValue: boardID)
        else { throw HostArguments.Failure.usage("Choose a read-only supported prepared base.") }
        if board == .n81, args.values["rom"] != nil {
            throw HostArguments.Failure.usage("n81 boots prepared kboot.bin; omit the separate ROM.")
        }
        if board == .n72, args.values["rom"] == nil {
            throw HostArguments.Failure.usage("The n72 route needs its matching boot asset.")
        }
        let lockBytes = try Data(contentsOf: base.appendingPathComponent("device.lock.json"))
        let hash = SHA256.hash(data: lockBytes).map { String(format: "%02x", $0) }.joined()
        let directory = DeviceInstance.directory(id, state: state)
        let record = directory.appendingPathComponent(DeviceRecord.name)
        if fm.fileExists(atPath: record.path) {
            instance = try DeviceInstance.read(record)
            guard instance.id == id, instance.board == boardID,
                instance.firmware == "\(boardID)-\(build)",
                StoragePathAuthority.canonicalPath(instance.paths(state: state, logs: state).base)
                    == StoragePathAuthority.canonicalPath(base),
                instance.storage.key == String(hash.prefix(16))
            else { throw HostArguments.Failure.usage("Saved native identity/base binding changed; files retained.") }
        } else {
            guard existing.allSatisfy({ $0 == ".shared-session-lease" }) else {
                throw HostArguments.Failure.usage("New state must be empty; native and Linux libraries are not converted.")
            }
            let relative = "Devices/\(id.uuidString)"
            instance = DeviceInstance(
                id: id, name: args.values["name"]!, board: boardID, firmware: "\(boardID)-\(build)",
                created: DeviceInstance.now, base: .init(kind: .prepared, path: base.path),
                storage: .init(key: String(hash.prefix(16)), overlay: relative + "/overlay",
                               writableNOR: fm.fileExists(atPath: base.appendingPathComponent("nor.bin").path)
                                   ? relative + "/nor.bin" : nil,
                               snapshot: relative + "/snapshot", usbmuxConf: relative + "/usbmuxd-conf"),
                identity: PreparationJob.identity(identityJSON: try? Data(contentsOf: base.appendingPathComponent("identity.json")),
                                                  lock: lock, seed: id.uuidString),
                provenance: .init(lock: base.appendingPathComponent("device.lock.json").path, sha256: hash)
            )
            try instance.write(state: state)
        }
        let paths = instance.paths(state: state, logs: state.appendingPathComponent("logs"))
        try StoragePathAuthority.checkBootPaths(base: paths.base,
            mutable: [paths.overlay, paths.snapshot, paths.usbmuxConf, paths.work, paths.lease] + [paths.writableNOR].compactMap { $0 },
            state: state, owner: id)
        // Inspect/create frontend markers under actual stopped native exclusion.
        let stopped = try StorageLease(paths.lease)
        defer { stopped.close() }
        if try markers.read(".dirty") != nil, !args.flags.contains("recover") {
            throw HostArguments.Failure.usage("Shutdown is unconfirmed; choose explicit interrupted-state recovery.")
        }
        if let pin = try markers.read(".base-identity") {
            guard String(data: pin, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) == hash else {
                throw HostArguments.Failure.usage("State belongs to another prepared base.")
            }
        } else { try markers.replace(".base-identity", with: Data(hash.utf8)) }
        try markers.replace(".dirty", with: dirtyBytes)
        capture = try ProcessLogCapture(url: paths.logs.appendingPathComponent("native.log"))
        var configuration = DeviceLink.Configuration(instance: id, outputDescriptor: capture.writeDescriptor)
        configuration.helper = args.url("helper")
        configuration.dylib = args.values["engine"]
        configuration.board = boardID
        configuration.lease = paths.lease
        process = DeviceSessionProcess(configuration: configuration)
    }

    func start() throws {
        window = try SharedGuestWindow(link: process.link, title: instance.name,
            stopRequested: { [weak self] in self?.stop() },
            screenshotRequested: { [weak self] surface in self?.screenshot(surface) },
            reportError: { hostLine("LightTouch: shared window failed: \($0)") })
        input = HostInput { [weak self] in self?.stop() }
        process.onTermination = { [weak self] _, termination, qemuExit in
            self?.termination = termination; self?.qemuExit = qemuExit
        }
        process.onDeath = { [weak self] _ in self?.finish() }
        process.start({ [weak self] info in
            guard let self else { return nil }
            do { return try self.configuration(info) } catch {
                hostLine("LightTouch: native boot assembly refused: \(error)"); return nil
            }
        }, preparation: { [weak self] in
            guard let self else { throw CancellationError() }
            let paths = self.instance.paths(state: self.state, logs: self.state.appendingPathComponent("logs"))
            _ = try await FirmwareTool.admitBoot(device: paths.directory, managed: true, executable: self.args.url("firmwarekit"))
            try Task.checkCancellation()
            let bytes = try Data(contentsOf: paths.directory.appendingPathComponent(DeviceRecord.name))
            let refreshed = try DeviceInstance.decoder.decode(DeviceInstance.self, from: bytes)
            guard refreshed.id == self.instance.id, refreshed.board == self.instance.board else {
                throw HostArguments.Failure.usage("Native identity changed during stopped admission.")
            }
            self.instance = refreshed
            self.admittedStorage = try StorageBootProof.capture(recordBytes: bytes)
            try Task.checkCancellation()
            let servicePaths = self.instance.paths(state: self.state, logs: self.state.appendingPathComponent("logs"))
            self.services = NativeAppServices.make(args: self.args, paths: servicePaths)
            if let services = self.services {
                services.start()
                if !(await services.waitReady()) {
                    services.beginRetirement()
                }
            }
            try Task.checkCancellation()
        }) { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let info):
                self.booted = true; self.window?.didBoot()
                hostLine("admission worker_pid=\(info.pid) fresh_worker=1")
            case .failure(let error): hostLine("LightTouch: native session start failed: \(error)")
            }
        }
        let timer = Timer(timeInterval: 1.0 / 30, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tick() }
        }
        RunLoop.main.add(timer, forMode: .common); self.timer = timer
    }

    private func configuration(_ info: HelperInfo) throws -> BootConfig {
        guard let board = instance.profile, let hardware = info.deviceInfo, let proof = admittedStorage,
            info.storageProofValidation == true else {
            throw HostArguments.Failure.usage("Native helper cannot validate this admitted board/storage generation.")
        }
        let paths = instance.paths(state: state, logs: state.appendingPathComponent("logs"))
        let prepared = try PreparedDeviceBoot.prepare(board: board, base: paths.base, overlay: paths.overlay,
            writableNOR: paths.writableNOR, storageKey: instance.storage.key, bootrom: args.values["rom"] ?? "",
            dieID: instance.identity?.dieID, panel: instance.panel)
        let offer = paths.work.appendingPathComponent("guest-offer")
        guard try GuestPackage.compose(itpack: args.url("package"), board: instance.board,
            build: instance.firmware.split(separator: "-").last.map(String.init) ?? "",
            lock: GuestPackage.lockRecord(try DeviceLock.read(base: paths.base)), guest: instance.guest, into: offer) != nil
        else { throw HostArguments.Failure.usage("No matching supported guest-tools package for this board/build.") }
        // Only this launch's admitted native USB bridge may enter BootConfig.
        // Networking remains unavailable; never inherit fixture endpoints.
        var config = try prepared.configuration(hardware: hardware, bootArgs: "-v", usbAddress: services?.guestAddress,
            wifi: false, guestPackage: offer.path, serial: "null",
            audio: args.flags.contains("no-audio") ? ["-audio", "driver=none"] : ["-audio", "driver=coreaudio,out.buffer-count=16"],
            netdev: nil)
        config.storageProof = proof
        // Chain TCG jumps across guest pages, as the Android and Linux ports do
        // (Portable/src/Storage.cpp has the measurements).
        config.argv += ["-accel", "tcg,cross-page-chain=on"]
        return config
    }

    func stop() {
        guard !stopping, !finished, !finishing else { return }
        stopping = true; shutdownStarted = Date(); window?.willStop()
        services?.willStop()
        if booted {
            process.link.send(.machine(.resume))
            process.link.send(.machine(.shutdown))
        } else {
            // Existing DeviceRuntime cancels preparation and exclusively reaps.
            forced = true; process.terminate()
        }
    }

    private func tick() {
        guard !finished else { return }
        window?.tick()
        services?.tick()
        let status = process.status
        if status?.shutdownConfirmed == true, !quitAfterConfirmation {
            if !stopping {
                stopping = true; shutdownStarted = Date(); window?.willStop()
                services?.willStop()
            }
            quitAfterConfirmation = true
            process.link.send(.machine(.quit)) // close only after guest confirmation
        }
        if status?.storageFailed == true { stop() }
        if !booted, Date().timeIntervalSince(started) > 600 { stop() }
        if let shutdownStarted {
            let elapsed = Date().timeIntervalSince(shutdownStarted)
            if elapsed > 45, !forced { forced = true; process.terminate() }
            if elapsed > 55, process.link.pid > 0 { forced = true; process.kill() }
            if elapsed > 65, process.link.pid > 0, !cleanupOverdue {
                cleanupOverdue = true
                // Retain this owner and its pipe until DeviceLink actually reaps.
                // A deadline is never permission to orphan the helper or emit
                // a terminal receipt before retirement. An external finite tree
                // supervisor remains necessary for a kernel-stuck process.
                hostLine("LightTouch: helper cleanup overdue; native owner retained; state remains dirty")
            }
        }
        if Date().timeIntervalSince(lastStatus) > 1 {
            lastStatus = Date()
            let state = stopping ? 4 : !booted ? 1 : window?.isPaused == true ? 3 : 2
            hostLine("status state=\(state) confirmed=\(status?.shutdownConfirmed == true ? 1 : 0) frames=\(status?.frameSerial ?? 0) dirty=1")
        }
    }

    private func finish(reaped: Bool = true) {
        guard !finished, !finishing else { return }
        finishing = true
        stopping = true; timer?.invalidate(); window?.willStop()
        services?.willStop()
        // A helper death does not prove transport retirement. Retain this native
        // owner until Subprocess.run has joined its exact daemon's teardown.
        Task { @MainActor [self] in
            let servicesClean = await services?.retire() ?? true
            finishAfterServices(reaped: reaped, servicesClean: servicesClean)
        }
    }

    private func finishAfterServices(reaped: Bool, servicesClean: Bool) {
        guard !finished else { return }
        finished = true; timer?.invalidate(); window?.willStop()
        let status = process.status
        var clean = booted && reaped && process.link.pid == 0 && termination == .exited(0) && qemuExit == 0
            && status?.shutdownConfirmed == true && status?.storageFailed == false && !forced && servicesClean
        if !servicesClean { hostLine("LightTouch: app-services cleanup unconfirmed; state remains dirty") }
        if clean {
            do {
                let paths = instance.paths(state: state, logs: state.appendingPathComponent("logs"))
                let stopped = try StorageLease(paths.lease)
                defer { stopped.close() }
                try markers.remove(".dirty", matching: dirtyBytes)
            } catch {
                clean = false
                // If a directory flush failed after unlink, conservatively
                // restore the interrupted marker before reporting failure.
                try? markers.replace(".dirty", with: dirtyBytes)
                hostLine("LightTouch: clean-state publication refused: \(error)")
            }
        }
        capture.flush()
        if reaped { window?.closeAfterReap() }
        let code = clean ? 0 : 1
        hostLine("FINAL status_available=\(status == nil ? 0 : 1) state=0 confirmed=\(status?.shutdownConfirmed == true ? 1 : 0) dirty=\(clean ? 0 : 1) worker_reaped=\(reaped && process.link.pid == 0 ? 1 : 0) worker_exit=\(qemuExit ?? -1) worker_disposal_requested=\(forced ? 1 : 0) frontend_return=\(code)")
        sessionLease.close()
        exit(Int32(code))
    }

    private func screenshot(_ surface: IOSurface) {
        // Native host screenshot only; does not send Home+Power to iOS.
        let directory = state.appendingPathComponent("screenshots")
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                     attributes: [.posixPermissions: 0o700])
            // Reuse the native image writer through the shared surface adapter.
            let image = try SharedSurfacePNG.data(surface)
            let path = directory.appendingPathComponent("host-\(UUID().uuidString).png")
            try image.write(to: path, options: .atomic)
            hostLine("LightTouch: host screenshot saved: \(path.path)")
        } catch { hostLine("LightTouch: screenshot failed: \(error)") }
    }
}

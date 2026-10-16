import CDesktopWindow
import DeviceRuntime
import Foundation
import IOSurface

/// Main-thread SDL presentation for an already owned native DeviceLink.
/// The caller retains existing storage admission, fresh DeviceSessionProcess,
/// guest-package offers, endpoints, audio and strict shutdown/reaping ownership.
/// This adapter never constructs a boot recipe or labels an exit clean.
@MainActor public final class SharedGuestWindow {
    public enum Failure: Error { case presentation(String) }

    private let link: DeviceLink
    private var window: OpaquePointer?
    private let stopRequested: () -> Void
    private let screenshotRequested: (IOSurface) -> Void
    private let reportError: (String) -> Void
    private var paused = false
    private var stopping = false
    private var faulted = false
    private var booted = false

    public init(
        link: DeviceLink,
        title: String,
        width: Int32 = 480,
        height: Int32 = 800,
        stopRequested: @escaping () -> Void,
        screenshotRequested: @escaping (IOSurface) -> Void,
        reportError: @escaping (String) -> Void
    ) throws {
        self.link = link
        self.stopRequested = stopRequested
        self.screenshotRequested = screenshotRequested
        self.reportError = reportError
        window = title.withCString { lt_desktop_window_create($0, width, height) }
        guard window != nil else {
            throw Failure.presentation(String(cString: lt_desktop_window_error()))
        }
    }

    isolated deinit {
        // Unexpected UI teardown releases inputs but never disposes a live
        // helper or removes its dirty marker/lease. The process owner remains.
        if let window {
            stopping = true
            lt_desktop_window_enabled(window, 0)
            drainEvents(window)
            lt_desktop_window_destroy(window)
        }
    }

    /// Called only after the existing process owner's boot completion succeeds.
    public func didBoot() {
        guard !stopping, !faulted, let window else { return }
        booted = true
        lt_desktop_window_enabled(window, 1)
        link.send(.screenVisible(true))
    }

    /// Call before external pause/Stop/surface loss to balance owned inputs.
    public func willStop() {
        guard let window else { return }
        stopping = true
        lt_desktop_window_enabled(window, 0)
        drainEvents(window)
    }

    /// The existing process owner calls this after confirmed helper termination.
    /// Destroying the SDL window says nothing about guest shutdown or persistence.
    public func closeAfterReap() {
        guard link.pid == 0, let window else { return }
        lt_desktop_window_cancel(window)
        drainEvents(window)
        lt_desktop_window_destroy(window)
        self.window = nil
    }

    /// One reader of DeviceLink.frontSurface; call from the main run loop.
    /// IOSurface pixels are copied synchronously into the shared opaque texture.
    /// There is no per-frame JSON/base64/PNG transport or second engine instance.
    public func tick() {
        guard let window, !faulted else { return }
        drainEvents(window)
        if let frame = link.frontSurface(), frame.isNew {
            let surface = frame.surface
            surface.lock(options: .readOnly, seed: nil)
            let result = lt_desktop_window_frame(
                window, surface.baseAddress, Int32(surface.width),
                Int32(surface.height), Int32(surface.bytesPerRow)
            )
            surface.unlock(options: .readOnly, seed: nil)
            if result != 0 { fail(window); return }
        }
        // A size change can emit releases when a new frame replaces the old one.
        drainEvents(window)
        if lt_desktop_window_present(window, paused ? 1 : 0, stopping ? 1 : 0) != 0 { fail(window) }
    }

    private func fail(_ window: OpaquePointer) {
        guard !faulted else { return }
        let message = String(cString: lt_desktop_window_error())
        faulted = true
        let alreadyStopping = stopping
        willStop()
        reportError(message)
        if !alreadyStopping { stopRequested() }
    }

    private func drainEvents(_ window: OpaquePointer) {
        var event = LTDesktopEvent()
        // Bound one UI turn. Pending cancellation is at most ten contacts and
        // four buttons; a busy SDL input queue must not starve host lifecycle.
        for _ in 0..<512 {
            guard lt_desktop_window_poll(window, &event) == 1 else { break }
            switch event.kind {
            case Int32(LT_DESKTOP_BUTTON.rawValue):
                link.send(.button(Int(event.identifier), down: event.phase != 0))
            case Int32(LT_DESKTOP_TOUCH.rawValue):
                link.send(.touch(slot: Int(event.identifier), phase: Int(event.phase), x: event.x, y: event.y))
            case Int32(LT_DESKTOP_PAUSE.rawValue):
                guard booted, !stopping else { continue }
                paused.toggle()
                link.send(.machine(paused ? .pause : .resume))
            case Int32(LT_DESKTOP_ROTATE.rawValue):
                guard booted, !stopping else { continue }
                // Upstream rotates the emulated device; no gyro qualification.
                link.send(.rotate(clockwise: true))
            case Int32(LT_DESKTOP_SCREENSHOT.rawValue):
                if let frame = link.frontSurface() { screenshotRequested(frame.surface) }
            case Int32(LT_DESKTOP_STOP.rawValue):
                guard !stopping else { continue }
                stopping = true
                // C window enqueued all releases before this callback. The
                // owner must use graceful shutdown and retain failed state.
                stopRequested()
            case Int32(LT_DESKTOP_VISIBLE.rawValue):
                link.send(.screenVisible(event.phase != 0))
            default: break
            }
        }
    }
}

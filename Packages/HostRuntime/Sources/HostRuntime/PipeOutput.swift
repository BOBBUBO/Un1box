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
#if canImport(os)
import os
#endif

/// A command's stream: one JSON line per event on stdout, diagnostics on stderr.
///
/// On Darwin a dispatch channel carries it, so a blocked write never occupies the caller; a closed reader is an
/// error the caller can join (`finish()`). Elsewhere -- and on Android in particular -- the write is the write:
/// `write(2)` to the descriptor, synchronously, with EPIPE and the like recorded the same way. There is no
/// dispatch thread to start before the first event, which is the difference that matters for a short-lived
/// command whose very first event is the one that says what it is doing.
#if canImport(Darwin)
public final class PipeOutput: Sendable {
    private struct State {
        var cancelled = false
        var pending = 0
        var failure: Int32?
        var waiters: [CheckedContinuation<Bool, Never>] = []
    }
    private let state = OSAllocatedUnfairLock(initialState: State())
    private let queue = DispatchQueue(label: "host-runtime.pipe-output")
    private let channel: DispatchIO

    public init(fileDescriptor: Int32) {
        // Darwin supports suppressing SIGPIPE on this descriptor without changing
        // signal dispositions inherited by unrelated subprocess descriptors.
        #if canImport(Darwin)
        let signalError = fcntl(fileDescriptor, F_SETNOSIGPIPE, 1) == -1 ? errno : 0
        #else
        let signalError: Int32 = 0  // elsewhere the host ignores SIGPIPE process-wide
        #endif
        channel = DispatchIO(type: .stream, fileDescriptor: fileDescriptor, queue: queue) { _ in }
        if signalError != 0 { state.withLock { $0.failure = signalError } }
    }
    public func stop() {
        state.withLock { $0.cancelled = true }
        channel.close(flags: .stop)
    }
    public func write(_ data: Data) {
        let bytes = data.withUnsafeBytes { DispatchData(bytes: $0) }
        // Submission is nonblocking; the native stream channel serializes writes.
        state.withLock { state in
            guard !state.cancelled else { return }
            state.pending += 1
            channel.write(offset: 0, data: bytes, queue: queue) { [self] done, _, error in
                guard done else { return }
                let (waiters, success) = self.state.withLock { current -> ([CheckedContinuation<Bool, Never>], Bool) in
                    current.pending -= 1
                    if error != 0 && !current.cancelled { current.failure = error }
                    guard current.pending == 0 else { return ([], false) }
                    let waiters = current.waiters
                    current.waiters.removeAll()
                    return (waiters, current.failure == nil)
                }
                for waiter in waiters { waiter.resume(returning: success) }
            }
        }
    }
    /// Joins every queued native callback. True means no recorded delivery
    /// error; after stop it does not certify that cancelled bytes were delivered.
    public func finish() async -> Bool {
        await withCheckedContinuation { continuation in
            let immediate = state.withLock { state -> Bool? in
                guard state.pending != 0 else { return state.failure == nil }
                state.waiters.append(continuation)
                return nil
            }
            if let immediate { continuation.resume(returning: immediate) }
        }
    }
}
#else
/// `write(2)`, apart from the class's own method of that name.
private func streamWrite(_ descriptor: Int32, _ buffer: UnsafeRawPointer, _ count: Int) -> Int {
    write(descriptor, buffer, count)
}

public final class PipeOutput: Sendable {
    private struct State {
        var cancelled = false
        var failure: Int32?
    }
    private let descriptor: Int32
    private let state = OSAllocatedUnfairLock(initialState: State())

    public init(fileDescriptor: Int32) {
        // SIGPIPE is ignored process-wide off Darwin (see suppressSIGPIPE), so a reader that is gone is EPIPE
        // from write(2), not a signal.
        descriptor = fileDescriptor
    }

    public func stop() { state.withLock { $0.cancelled = true } }

    public func write(_ data: Data) {
        guard !data.isEmpty else { return }
        data.withUnsafeBytes { (buffer: UnsafeRawBufferPointer) in
            guard let base = buffer.baseAddress else { return }
            var offset = 0
            while offset < buffer.count {
                let n = streamWrite(descriptor, base + offset, buffer.count - offset)
                guard n >= 0 else {
                    if errno == EINTR { continue }
                    let failure = errno
                    state.withLock { current in
                        if !current.cancelled { current.failure = failure }
                    }
                    return
                }
                offset += n
            }
        }
    }

    /// Nothing is queued: a write has landed by the time it returns. True means no write failed.
    public func finish() async -> Bool { state.withLock { $0.failure == nil } }
}
#endif

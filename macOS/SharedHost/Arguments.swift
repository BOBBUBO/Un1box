import Darwin
import Foundation

struct HostArguments: Sendable {
    let operation: String
    let values: [String: String]
    let flags: Set<String>
    enum Failure: Error { case usage(String) }

    init(_ argv: [String]) throws {
        guard let operation = argv.first, ["run", "import"].contains(operation) else {
            throw Failure.usage("Use run or import with explicit shared-desktop paths.")
        }
        let allowed: Set<String> = operation == "run"
            ? ["id", "name", "base", "state", "package", "engine", "helper", "firmwarekit", "rom"]
            : ["source", "destination", "sha256"]
        var values: [String: String] = [:]
        var flags: Set<String> = []
        var index = 1
        while index < argv.count {
            let option = argv[index]
            guard option.hasPrefix("--") else { throw Failure.usage("Expected a named option.") }
            let key = String(option.dropFirst(2))
            if operation == "run", ["recover", "no-audio"].contains(key) {
                guard flags.insert(key).inserted else { throw Failure.usage("Duplicate flag.") }
                index += 1; continue
            }
            guard allowed.contains(key), values[key] == nil, index + 1 < argv.count,
                !argv[index + 1].isEmpty, !argv[index + 1].contains("\0")
            else { throw Failure.usage("Missing, duplicate or unsupported option: \(key)") }
            values[key] = argv[index + 1]; index += 2
        }
        let required = allowed.subtracting(operation == "run" ? ["rom"] : [])
        guard required.isSubset(of: Set(values.keys)) else { throw Failure.usage("Required option missing.") }
        for (key, value) in values where !["id", "name", "sha256"].contains(key) {
            guard value.hasPrefix("/"), !value.split(separator: "/").contains("..") else {
                throw Failure.usage("Use an absolute path without traversal: \(key)")
            }
        }
        self.operation = operation; self.values = values; self.flags = flags
    }
    func url(_ key: String) -> URL { URL(fileURLWithPath: values[key]!) }
}

/// One bounded input line over the launcher's private inherited pipe. EOF asks
/// the same ordinary Stop; it is not a success receipt or a force-quit signal.
@MainActor final class HostInput {
    private var source: DispatchSourceRead?
    private var bytes = Data()
    init(stop: @escaping @MainActor () -> Void) {
        let source = DispatchSource.makeReadSource(fileDescriptor: STDIN_FILENO, queue: .main)
        source.setEventHandler { [weak self] in
            MainActor.assumeIsolated {
                guard let self else { return }
                var buffer = [UInt8](repeating: 0, count: 512)
                let count = Darwin.read(STDIN_FILENO, &buffer, buffer.count)
                guard count > 0 else { self.source?.cancel(); stop(); return }
                self.bytes.append(contentsOf: buffer.prefix(count))
                if self.bytes.count > 4096 { self.source?.cancel(); stop(); return }
                while let end = self.bytes.firstIndex(of: 10) {
                    let line = self.bytes.prefix(upTo: end)
                    self.bytes.removeSubrange(...end)
                    if line == Data("stop".utf8) { stop() }
                }
            }
        }
        self.source = source; source.resume()
    }
    isolated deinit { source?.cancel() }
}

nonisolated func hostLine(_ text: String) {
    // Native helper output goes into its own bounded log, never this receipt pipe.
    try? FileHandle.standardOutput.write(contentsOf: Data((text + "\n").utf8))
}

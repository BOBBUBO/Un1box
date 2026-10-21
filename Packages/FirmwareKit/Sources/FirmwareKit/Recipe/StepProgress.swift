// The contract's `progress` events for Preparer.create: one per second while a step runs, plus a 1.0
// when it ends. The fraction is measured where the step can say (bytes hashed) and otherwise follows
// the step's plan: serial-log milestones of the one-shot boots, and elapsed time against the expected
// seconds in between. It never goes back and stays below 1 until the step is over.
//
//   let p = StepProgress(emit: emit)          // heartbeat thread until p.stop()
//   p.next(index: 3, name: "Writing the NAND") // ends the previous step (1.0), emits the step event
//   p.measure = { hashed / total }             // optional, for this step only
//   p.finish()                                 // the last step's 1.0

import Foundation

/// What one step expects: its seconds on a recent Mac, what to say, and (boots) the serial-log lines it passes.
struct StepPlan: Sendable {
    /// A line in a serial log under the work directory, `at` seconds into the step. `file` ending in "-" is a
    /// prefix: the newest match (keybag-1.log, keybag-2.log on a retry).
    struct Milestone: Sendable { var file: String, marker: String, at: Double, text: String }
    var seconds: Double, text: String, milestones: [Milestone] = []

    // Timings: firmwarekit create of 7B500 and 8C148 on an M4 Max, 2026-09-28 (the seal is most of it; its
    // flash indexing took 1-12 s and the NAND 3-13 s across runs).
    static let plans: [String: StepPlan] = [
        "Verifying the IPSW": .init(seconds: 2, text: "Checking the IPSW’s checksum"),
        "Decrypting the firmware": .init(seconds: 5, text: "Decrypting the firmware"),
        "Writing the identity and boot image": .init(seconds: 1, text: "Writing the boot image"),
        "Building the system and data volumes": .init(seconds: 12, text: "Building the system and data volumes"),
        "Writing the NAND": .init(seconds: 8, text: "Writing the flash image"),
        "Creating the data-protection keybag": .init(
            seconds: 6,
            text: "Encryption setup: starting",
            milestones: [
                .init(file: "keybag-", marker: "FTL_Open", at: 2, text: "Encryption setup: opening the flash"),
                .init(file: "keybag-", marker: "it_keybag:", at: 4, text: "Encryption setup: creating the keybag"),
            ]
        ),
        // The seal one-shot (Recipe.sealStep): boot the new flash until it_seal halts. Each line is
        // one every release prints; a release that skips one moves on by time (fraction(elapsed:seen:)).
        sealStep: .init(
            seconds: 68,
            text: "First start: loading iOS",
            milestones: [
                .init(file: "seal.log", marker: "CXT is not valid", at: 1, text: "First start: indexing the flash"),
                .init(file: "seal.log", marker: "FTL_Open", at: 6, text: "First start: opening the flash"),
                .init(file: "seal.log", marker: "launchd[1] has started", at: 19, text: "First start: starting iOS"),
                .init(file: "seal.log", marker: "SpringBoard[", at: 30, text: "First start: starting the Home screen"),
                .init(file: "seal.log", marker: "it_prefs:", at: 41, text: "First start: starting the Home screen"),
                .init(file: "seal.log", marker: "it_seal:", at: 65, text: "First start: shutting down"),
            ]
        ),
        "Writing the lock": .init(seconds: 3, text: "Hashing the prepared flash"),
        // n72 (N72Recipe; ~35 s for 7E18)
        "Writing the identity, NOR and boot files": .init(seconds: 2, text: "Writing the NOR and boot files"),
        "Building the system volume": .init(seconds: 20, text: "Building the system volume"),
        // 4.x data protection (N72Keybag): iBoot, the kernel entry handoff, then the ramdisk's it_keybag
        "Booting the restore ramdisk": .init(
            seconds: 40,
            text: "Encryption setup: starting",
            milestones: [
                .init(
                    file: "keybag.log",
                    marker: "FTL_Open",
                    at: 10,
                    text: "Encryption setup: opening the flash"
                ),
                .init(file: "keybag.log", marker: "it_keybag:", at: 30, text: "Encryption setup: creating the keybag"),
            ]
        ),
    ]

    /// The step that boots the prepared flash once so its first boot is done (it_seal halts it).
    static let sealStep = "Finishing setup"

    /// iOS 7's seal boot: launchd starts our daemons ~3 minutes in (K48Recipe.oneshotTimeout), so SpringBoard and
    /// the halt come late. n90ap-11D257 on an M4 Max under load, 2026-10-07: launchd 8 s, Wi-Fi 60 s, SpringBoard
    /// 204 s, lockdown 239 s, it_seal 328 s, halted by 331 s.
    static let seal7 = StepPlan(
        seconds: 331,
        text: "First start: loading iOS",
        milestones: [
            .init(file: "seal.log", marker: "FTL_Open", at: 4, text: "First start: opening the flash"),
            .init(file: "seal.log", marker: "launchd[1] has started", at: 8, text: "First start: starting iOS"),
            .init(file: "seal.log", marker: "AirPort: Link Up", at: 60, text: "First start: starting iOS"),
            .init(file: "seal.log", marker: "SpringBoard[", at: 204, text: "First start: starting the Home screen"),
            .init(file: "seal.log", marker: "lockdown says", at: 239, text: "First start: starting the Home screen"),
            .init(file: "seal.log", marker: "it_seal:", at: 328, text: "First start: shutting down"),
        ]
    )

    /// iOS 6's seal boot: after it_prefs, the first-boot data migration runs and it_seal waits for it and for
    /// SpringBoard's first Home screen (it_seal.c). n81ap-10B500 on a Ryzen desktop under Linux, 2026-10-20: launchd 9 s,
    /// Wi-Fi 42 s, it_prefs 68 s, it_seal 192 s, halted by 195 s; with the assets preinstalled and the dead services off
    /// (SystemEdits) the seal took 58% as long in an interleaved A/B on a slower host, so it_seal now ~125 s.
    static let seal6 = StepPlan(
        seconds: 128,
        text: "First start: loading iOS",
        milestones: [
            .init(file: "seal.log", marker: "FTL_Open", at: 4, text: "First start: opening the flash"),
            .init(file: "seal.log", marker: "launchd[1] has started", at: 9, text: "First start: starting iOS"),
            .init(file: "seal.log", marker: "AirPort: Link Up", at: 42, text: "First start: starting iOS"),
            .init(file: "seal.log", marker: "it_prefs:", at: 68, text: "First start: setting up iOS"),
            .init(file: "seal.log", marker: "it_seal:", at: 125, text: "First start: shutting down"),
        ]
    )

    /// iOS 5's seal boot: after it_prefs, DataMigrator builds the first-boot databases (Photos, Media, Mail) and then
    /// SpringBoard lays out the Home screen, which it_seal waits for (it prints nothing in between). n88ap-9B206 on a
    /// Ryzen desktop under Linux, 2026-10-21: launchd 2 s, it_prefs 21 s, it_seal 87 s, halted by 95 s; a phone takes ~3x.
    static let seal5 = StepPlan(
        seconds: 95,
        text: "First start: loading iOS",
        milestones: [
            .init(file: "seal.log", marker: "FTL_Open", at: 1, text: "First start: opening the flash"),
            .init(file: "seal.log", marker: "launchd[1] has started", at: 2, text: "First start: starting iOS"),
            .init(file: "seal.log", marker: "it_prefs:", at: 21, text: "First start: setting up iOS"),
            .init(file: "seal.log", marker: "it_seal:", at: 87, text: "First start: shutting down"),
        ]
    )

    /// iOS 6's keybag boot, same machine: the restore ramdisk opens the flash at 6 s and it_keybag is done at 23 s,
    /// halted by 49 s. On a new flash the FTL first rebuilds its index, minutes on a phone.
    static let keybag6 = StepPlan(
        seconds: 49,
        text: "Encryption setup: starting",
        milestones: [
            .init(file: "keybag-", marker: "FTL_Open", at: 6, text: "Encryption setup: opening the flash"),
            .init(file: "keybag-", marker: "it_keybag:", at: 23, text: "Encryption setup: creating the keybag"),
        ]
    )

    /// `major`: the firmware's iOS major version (the seal boot of 6.x and 7.x takes several times as long).
    static func plan(_ name: String, major: Int = 0) -> StepPlan {
        if name == sealStep, major >= 7 { return seal7 }
        if name == sealStep, major == 6 { return seal6 }
        if name == sealStep, major == 5 { return seal5 }
        if name == "Creating the data-protection keybag", major == 6 { return keybag6 }
        return plans[name] ?? .init(seconds: 10, text: name)
    }

    /// 0..<1 at `elapsed` s: the last milestone seen (`seen[i]`, when it was first seen) plus the time since. Time
    /// alone runs on past milestones a release never prints (7.x has no it_prefs line) but never reaches the last one:
    /// only the boot's own halt and check finish the step. On by the clock for 80% of the way to the next milestone,
    /// then ever more slowly (same slope at the join): a slower host (a phone, 3-5x a Mac) never sees the bar stop.
    func fraction(elapsed: Double, seen: [Double?]) -> Double {
        let reached = seen.lastIndex { $0 != nil }
        let from = reached.map { milestones[$0].at } ?? 0
        let since = reached.flatMap { seen[$0] } ?? 0
        let to =
            reached.map { $0 + 1 < milestones.count ? (milestones.last?.at ?? seconds) : seconds }
            ?? (milestones.last?.at ?? seconds)
        let x = max(elapsed - since, 0), gap = max(to - from, 0), knee = 0.8 * gap, tail = 0.19 * gap
        let t = from + (x <= knee || tail <= 0 ? min(x, knee) : knee + tail * (1 - exp(-(x - knee) / tail)))
        return min(0.99, t / seconds)
    }
}

final class StepProgress: @unchecked Sendable {
    private let emit: @Sendable (PrepareEvent) -> Void
    private let work: URL?
    private let lock = NSLock(), done = DispatchSemaphore(value: 0), stopped = DispatchSemaphore(value: 0)
    private var plan: StepPlan?, text = "", started = Date(), last = 0.0, seen: [Double?] = [],
        measured: (@Sendable () -> Double)?

    /// `work` holds the one-shots' serial logs.
    init(work: URL?, major: Int = 0, emit: @escaping @Sendable (PrepareEvent) -> Void) {
        self.emit = emit
        self.major = major
        self.work = work
        Thread.detachNewThread { [self] in
            while done.wait(timeout: .now() + 1) == .timedOut { lock.withLock { tick() } }
            stopped.signal()
        }
    }

    /// This step's own fraction (0...1), e.g. bytes hashed; the plan's time estimate otherwise.
    var measure: (@Sendable () -> Double)? {
        get { lock.withLock { measured } }
        set { lock.withLock { measured = newValue } }
    }

    /// The firmware's iOS major version: picks the seal boot's plan (StepPlan.plan).
    let major: Int

    /// Ends the current step with 1.0, then emits the step event and the new step's first progress.
    func next(index: Int, name: String) {
        lock.withLock {
            end()
            emit(.step(index: index, name: name))
            let plan = StepPlan.plan(name, major: major)
            self.plan = plan
            text = plan.text
            started = Date()
            last = 0
            seen = Array(repeating: nil, count: plan.milestones.count)
            measured = nil
            tick()
        }
    }

    /// The last step's 1.0.
    func finish() { lock.withLock { end() } }

    /// Stops the heartbeat; no events after it returns.
    func stop() {
        done.signal()
        stopped.wait()
    }

    private func end() {
        guard plan != nil else { return }
        emit(.progress(1, detail: detail(text)))
        self.plan = nil
    }

    private func detail(_ text: String) -> String { "\(text) — \(Int(Date().timeIntervalSince(started))) s" }

    private func tick() {
        guard let plan else { return }
        let elapsed = Date().timeIntervalSince(started)
        if let work, !plan.milestones.isEmpty {
            var logs: [String: String] = [:]
            for (i, m) in plan.milestones.enumerated() {
                if seen[i] == nil {
                    let serial = logs[m.file] ?? Self.log(work, m.file)
                    logs[m.file] = serial
                    if serial.contains(m.marker) { seen[i] = elapsed }
                }
                if seen[i] != nil { text = m.text }
            }
        }
        let f = max(last, min(0.99, measured?() ?? plan.fraction(elapsed: elapsed, seen: seen)))
        last = f
        emit(.progress((f * 1000).rounded() / 1000, detail: detail(text)))
    }

    /// A serial log's text ("" until it exists); a "prefix-" names the newest of prefix*.log.
    static func log(_ work: URL, _ file: String) -> String {
        var name = file
        if file.hasSuffix("-") {
            let names = (try? FileManager.default.contentsOfDirectory(atPath: work.path)) ?? []
            guard
                let newest = names.filter({ $0.hasPrefix(file) && $0.hasSuffix(".log") })
                    .max(by: { $0.localizedStandardCompare($1) == .orderedAscending })
            else { return "" }
            name = newest
        }
        return (try? String(contentsOf: work.appendingPathComponent(name), encoding: .isoLatin1)) ?? ""
    }
}

/// Bytes done of a total, as a step's `measure`.
final class ByteCount: @unchecked Sendable {
    private let total: Int, lock = NSLock()
    private var done = 0
    init(total: Int) { self.total = total }
    func add(_ n: Int) { lock.withLock { done += n } }
    var fraction: Double { lock.withLock { total > 0 ? Double(done) / Double(total) : 0 } }
}

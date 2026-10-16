import Cocoa
import LightTouchCore

#if LIGHTTOUCH_CLT_BUILD
    // The development bundle must not sweep or open the installed app's
    // device library. An explicit override still selects a private test root.
    if ProcessInfo.processInfo.environment["LTM_STATE_DIR"] == nil {
        let root = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("org.lighttouch.native-test/State", isDirectory: true)
        guard setenv("LTM_STATE_DIR", root.path, 0) == 0 else {
            fatalError("Cannot select the native test app's isolated device library")
        }
    }
#endif

// Top-level, so it is retained for the process lifetime (NSApplication.delegate
// is a weak reference). Program entry is the main thread; assumeIsolated lets
// the delegate's MainActor-isolated conformance be assigned without a hop.
logEvent("Light Touch started")
let delegate = MainActor.assumeIsolated {
    RestorationDefaults.configure()
    return AppDelegate()
}
MainActor.assumeIsolated {
    LightTouchApplication.shared.delegate = delegate
}
_ = NSApplicationMain(CommandLine.argc, CommandLine.unsafeArgv)

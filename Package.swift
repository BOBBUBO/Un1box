// swift-tools-version: 6.2
// Command Line Tools build of the existing native Cocoa frontend. The Xcode
// project remains the release/archive route; see macOS/README.md.
import PackageDescription
import Foundation

let sourceRoot = URL(fileURLWithPath: #filePath).deletingLastPathComponent().path
let vendor = ProcessInfo.processInfo.environment["LIGHTTOUCH_MAC_VENDOR_CONTENTS"]
    ?? "/Applications/Light Touch.app/Contents"
let headers = ProcessInfo.processInfo.environment["LIGHTTOUCH_MAC_C_HEADERS"]
    ?? "/opt/homebrew/include"
let isolation: [SwiftSetting] = [
    .defaultIsolation(MainActor.self),
    .enableUpcomingFeature("NonisolatedNonsendingByDefault"),
    .enableUpcomingFeature("InferIsolatedConformances"),
    .enableUpcomingFeature("MemberImportVisibility"),
]
let package = Package(
    name: "LightTouchMacNative",
    platforms: [.macOS("14.4")],
    products: [
        .executable(name: "LightTouch", targets: ["LightTouch"]),
        .executable(name: "LightTouchDevice", targets: ["LightTouchDevice"]),
        .executable(name: "LightTouchServices", targets: ["LightTouchServices"]),
        // Incremental shared SDL adapter; Cocoa remains the reference until
        // native shared launcher/admission/packaging acceptance is complete.
        .library(name: "LightTouchSharedDesktopMac", targets: ["LightTouchSharedDesktopMac"]),
    ],
    dependencies: [
        .package(path: "Packages/HostRuntime"),
        .package(path: "Packages/DeviceRuntime"),
        .package(path: "Packages/DeviceServices"),
        .package(path: "Packages/FirmwareKit"),
        .package(path: "Packages/LightTouchCore"),
        .package(url: "https://github.com/weichsel/ZIPFoundation.git", exact: "0.9.20"),
        .package(url: "https://github.com/sparkle-project/Sparkle", exact: "2.10.0"),
    ],
    targets: [
        .systemLibrary(name: "CDesktopSDL", path: "Desktop/SDL2", pkgConfig: "sdl2",
                       providers: [.brew(["sdl2"])]),
        .target(name: "CDesktopWindow", dependencies: ["CDesktopSDL"], path: "Desktop/native",
                publicHeadersPath: "include", cxxSettings: [.unsafeFlags(["-std=c++17"])]),
        .target(name: "LightTouchSharedDesktopMac",
                dependencies: ["CDesktopWindow", .product(name: "DeviceRuntime", package: "DeviceRuntime")],
                path: "macOS/SharedDesktop"),
        .executableTarget(
            name: "LightTouch",
            dependencies: [
                .product(name: "HostRuntime", package: "HostRuntime"),
                .product(name: "DeviceRuntime", package: "DeviceRuntime"),
                .product(name: "HostServiceWire", package: "DeviceServices"),
                .product(name: "HostServiceClient", package: "DeviceServices"),
                .product(name: "FirmwareSchema", package: "FirmwareKit"),
                .product(name: "LightTouchCore", package: "LightTouchCore"),
                .product(name: "ZIPFoundation", package: "ZIPFoundation"),
                .product(name: "Sparkle", package: "Sparkle"),
            ],
            path: "LightTouchMac",
            exclude: ["Assets.xcassets", "LightTouch.icon", "Resources", "Help.txt", "Sleeping.caar", "special_trick.mp3"],
            swiftSettings: isolation + [.define("LIGHTTOUCH_CLT_BUILD")]
        ),
        .target(
            name: "CGuestTLS", path: "LightTouchDevice", sources: ["GuestTLS.c"],
            publicHeadersPath: ".", linkerSettings: [.linkedFramework("Security")]
        ),
        .executableTarget(
            name: "LightTouchDevice",
            dependencies: ["CGuestTLS", .product(name: "HostRuntime", package: "HostRuntime"),
                           .product(name: "DeviceRuntime", package: "DeviceRuntime")],
            path: "LightTouchDevice", exclude: ["GuestTLS.c", "GuestTLS.h"],
            swiftSettings: [.unsafeFlags(["-import-objc-header", sourceRoot + "/LightTouchDevice/GuestTLS.h"])],
            linkerSettings: [.linkedFramework("Security"), .linkedFramework("IOSurface"),
                .unsafeFlags(["-Xlinker", "-sectcreate", "-Xlinker", "__TEXT", "-Xlinker", "__info_plist",
                              "-Xlinker", sourceRoot + "/Configuration/LightTouchDevice-Info.plist"])]
        ),
        .target(
            name: "CLockdown", path: "LightTouchServices/Lockdown",
            sources: ["lockdown-ddi.c", "lockdown-mcinstall.c", "lockdown-tz.c"],
            publicHeadersPath: ".", cSettings: [.unsafeFlags(["-I", headers])],
            linkerSettings: [.unsafeFlags(["-L", vendor + "/Frameworks"]),
                             .linkedLibrary("imobiledevice-1.0"), .linkedLibrary("plist-2.0")]
        ),
        .executableTarget(
            name: "LightTouchServices",
            dependencies: ["CLockdown", .product(name: "HostServiceWire", package: "DeviceServices")],
            path: "LightTouchServices", exclude: ["Lockdown"],
            swiftSettings: isolation + [.unsafeFlags(["-import-objc-header", sourceRoot + "/LightTouchServices/Lockdown/Lockdown.h", "-Xcc", "-I" + headers])]
        ),
    ],
    swiftLanguageModes: [.v6]
)

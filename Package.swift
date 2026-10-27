// swift-tools-version: 6.2
// Command Line Tools build of the macOS port (macOS/build-shared.py): the SDL
// host and the LightTouchDevice engine helper. See macOS/README.md.
import PackageDescription
import Foundation

let sourceRoot = URL(fileURLWithPath: #filePath).deletingLastPathComponent().path
let package = Package(
    name: "LightTouchMacNative",
    platforms: [.macOS("14.4")],
    products: [
        .executable(name: "LightTouchDevice", targets: ["LightTouchDevice"]),
        .library(name: "LightTouchSharedDesktopMac", targets: ["LightTouchSharedDesktopMac"]),
        .executable(name: "LightTouchDesktopHost", targets: ["LightTouchDesktopHost"]),
    ],
    dependencies: [
        .package(path: "Packages/HostRuntime"),
        .package(path: "Packages/DeviceRuntime"),
        .package(path: "Packages/FirmwareKit"),
        .package(path: "Packages/LightTouchCore"),
    ],
    targets: [
        .systemLibrary(name: "CDesktopSDL", path: "Desktop/SDL2", pkgConfig: "sdl2",
                       providers: [.brew(["sdl2"])]),
        .target(name: "CDesktopWindow", dependencies: ["CDesktopSDL"], path: "Desktop/native",
                publicHeadersPath: "include", cxxSettings: [.unsafeFlags(["-std=c++17"])]),
        .target(name: "LightTouchSharedDesktopMac",
                dependencies: ["CDesktopWindow", .product(name: "DeviceRuntime", package: "DeviceRuntime")],
                path: "macOS/SharedDesktop"),
        .executableTarget(name: "LightTouchDesktopHost",
                dependencies: ["LightTouchSharedDesktopMac",
                    .product(name: "HostRuntime", package: "HostRuntime"),
                    .product(name: "DeviceRuntime", package: "DeviceRuntime"),
                    .product(name: "FirmwareKit", package: "FirmwareKit"),
                    .product(name: "LightTouchCore", package: "LightTouchCore")],
                path: "macOS/SharedHost"),
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
    ],
    swiftLanguageModes: [.v6]
)

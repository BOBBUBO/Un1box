// swift-tools-version: 6.0
import PackageDescription

// Off Apple platforms (Linux, Android, Windows), swift-crypto stands in for CryptoKit.
let nonApple: [Platform] = [.linux, .android, .windows]

let package = Package(
    name: "HostRuntime",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "HostRuntime", type: .static, targets: ["HostRuntime"]),
        // Apple's SDKs have a zlib module; elsewhere this one maps the system library to the same name.
        .library(name: "zlib", targets: ["zlib"]),
        .library(name: "CPortability", targets: ["CPortability"]),
    ],
    dependencies: [.package(url: "https://github.com/apple/swift-crypto.git", exact: "3.15.1")],
    targets: [
        .systemLibrary(name: "zlib"),
        .target(name: "CPortability"),
        .target(name: "HostRuntime",
                dependencies: [.target(name: "zlib", condition: .when(platforms: nonApple)),
                               .target(name: "CPortability", condition: .when(platforms: nonApple)),
                               .product(name: "Crypto", package: "swift-crypto", condition: .when(platforms: nonApple))]),
        .testTarget(name: "HostRuntimeTests", dependencies: ["HostRuntime"]),
    ]
)

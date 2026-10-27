import Foundation
@_spi(FirmwareMemoryQualification) import FirmwareKit

// Deliberately no SwiftTesting/XCTest/ArgumentParser or historical SDK dependency.
// The standalone process calls the real FirmwareKit algorithms through a narrow SPI.
let args = Array(CommandLine.arguments.dropFirst())
guard args.count == 3, args[1] == "--out", ["ordinary", "sparse"].contains(args[0]), args[2].hasPrefix("/") else {
    FileHandle.standardError.write(Data("usage: firmware-memory-probe ordinary|sparse --out /fresh/private/path\n".utf8))
    exit(2)
}
do {
    let report = try FirmwareMemoryQualification.run(mode: args[0], out: URL(fileURLWithPath: args[2]))
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    var data = try encoder.encode(report)
    data.append(0x0A)
    FileHandle.standardOutput.write(data)
} catch {
    FileHandle.standardError.write(Data("firmware-memory-probe failed: \(error)\n".utf8))
    exit(1)
}

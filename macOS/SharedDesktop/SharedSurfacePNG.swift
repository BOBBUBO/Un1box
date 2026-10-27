import CoreGraphics
import Foundation
import ImageIO
import IOSurface
import UniformTypeIdentifiers

/// Synchronous host screenshot of the same opaque BGRA pixels SDL presents.
public enum SharedSurfacePNG {
    public enum Failure: Error { case invalidSurface, encoding }
    public static func data(_ surface: IOSurface) throws -> Data {
        let width = surface.width, height = surface.height, pitch = surface.bytesPerRow
        guard width > 0, height > 0, width <= 8192, height <= 8192,
            pitch >= width * 4, pitch <= 1024 * 1024, pitch * height <= 256 * (1024 * 1024)
        else { throw Failure.invalidSurface }
        surface.lock(options: .readOnly, seed: nil)
        let bytes = Data(bytes: surface.baseAddress, count: pitch * height)
        surface.unlock(options: .readOnly, seed: nil)
        guard let provider = CGDataProvider(data: bytes as CFData),
            let space = CGColorSpace(name: CGColorSpace.sRGB),
            let image = CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32,
                bytesPerRow: pitch, space: space,
                bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.noneSkipFirst.rawValue)
                    .union(.byteOrder32Little),
                provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent)
        else { throw Failure.invalidSurface }
        let output = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(output, UTType.png.identifier as CFString, 1, nil) else {
            throw Failure.encoding
        }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else { throw Failure.encoding }
        return output as Data
    }
}

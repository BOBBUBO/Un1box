import Foundation
import zlib

extension Data {
    /// Inflates a raw deflate stream (no zlib header), as NSData's `.zlib` decompression does on Apple
    /// platforms; there for Linux, Android and Windows, whose Foundation has no compression.
    func rawInflated() throws -> Data {
        var stream = z_stream()
        guard inflateInit2_(&stream, -MAX_WBITS, ZLIB_VERSION, Int32(MemoryLayout<z_stream>.size)) == Z_OK else {
            throw CocoaError(.fileReadCorruptFile)
        }
        defer { inflateEnd(&stream) }
        var out = Data()
        var buffer = [UInt8](repeating: 0, count: 1 << 16)
        var input = [UInt8](self)
        var status = Z_OK
        try input.withUnsafeMutableBufferPointer { inBuf in
            stream.next_in = inBuf.baseAddress
            stream.avail_in = uInt(inBuf.count)
            repeat {
                status = buffer.withUnsafeMutableBufferPointer { outBuf in
                    stream.next_out = outBuf.baseAddress
                    stream.avail_out = uInt(outBuf.count)
                    return inflate(&stream, Z_NO_FLUSH)
                }
                guard status == Z_OK || status == Z_STREAM_END else { throw CocoaError(.fileReadCorruptFile) }
                out.append(buffer, count: buffer.count - Int(stream.avail_out))
            } while status != Z_STREAM_END && (stream.avail_in > 0 || stream.avail_out == 0)
        }
        guard status == Z_STREAM_END else { throw CocoaError(.fileReadCorruptFile) }
        return out
    }
}

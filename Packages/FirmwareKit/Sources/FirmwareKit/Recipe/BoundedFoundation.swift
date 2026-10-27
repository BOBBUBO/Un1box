import Foundation

/// Foundation file reads/writes and path construction can autorelease objects on
/// Darwin. Long firmware loops must drain those temporaries per bounded unit of
/// work, rather than retaining them until the entire preparation task returns.
/// Non-Objective-C Foundation implementations use ordinary lexical lifetimes.
@inline(__always)
func withFirmwareAutoreleasePool<T>(_ body: () throws -> T) rethrows -> T {
    #if canImport(ObjectiveC)
        return try autoreleasepool(invoking: body)
    #else
        return try body()
    #endif
}

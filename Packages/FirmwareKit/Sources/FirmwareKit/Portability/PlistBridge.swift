import Foundation

/// SystemEdits edits plists as trees of NSMutableDictionary and NSMutableArray, changing nested containers through
/// references. On Apple platforms PropertyListSerialization's .mutableContainers gives exactly that, and serializes
/// such a tree back. Elsewhere (swift-corelibs-foundation) it gives Swift values, which neither cast to the NS
/// mutable types nor share edits, so the tree is converted on the way in and out. On Apple platforms both are the
/// identity.
enum PlistBridge {
    /// `value` with every dictionary and array as its NSMutable counterpart, all the way down.
    static func mutable(_ value: Any) -> Any {
        #if canImport(Darwin)
        return value
        #else
        if let d = value as? [String: Any] {
            let out = NSMutableDictionary()
            for (k, v) in d { out[k] = mutable(v) }
            return out
        }
        if let a = value as? [Any] { return NSMutableArray(array: a.map(mutable)) }
        return value
        #endif
    }

    /// The plist at `url` as a mutable dictionary tree, or nil when it is not a dictionary plist.
    static func mutableDictionary(contentsOf url: URL) -> NSMutableDictionary? {
        #if canImport(Darwin)
        return NSMutableDictionary(contentsOf: url)
        #else
        guard let data = try? Data(contentsOf: url),
            let plist = try? PropertyListSerialization.propertyList(from: data, format: nil)
        else { return nil }
        return mutable(plist) as? NSMutableDictionary
        #endif
    }

    /// `value` as Swift dictionaries, arrays, strings and numbers, which every Foundation serializes.
    static func native(_ value: Any) -> Any {
        #if canImport(Darwin)
        return value
        #else
        // Swift values first, by exact type: on this Foundation `as? Bool` also takes an NSNumber 0 or 1.
        let t = type(of: value)
        if t == Bool.self || t == Int.self || t == Double.self || t == String.self { return value }
        switch value {
        case let s as String: return s
        case let n as NSNumber:
            switch String(cString: n.objCType) {
            case "c", "B": return n.boolValue
            case "f", "d": return n.doubleValue
            default: return n.intValue
            }
        case let d as Data: return d
        case let d as Date: return d
        case let d as NSDictionary:
            var out: [String: Any] = [:]
            for (k, v) in d { out["\(k)"] = native(v) }
            return out
        case let a as NSArray: return a.map(native)
        case let d as [String: Any]: return d.mapValues(native)
        case let a as [Any]: return a.map(native)
        default: return value
        }
        #endif
    }
}

import Foundation

/// Bundle.preferredLocalizations(from:forPreferences:) for hosts whose Foundation does not implement it (Linux,
/// Android, Windows): the first preferred language that `supported` has, by the same fallbacks Apple's matcher
/// takes (zh-HK to zh-Hant, en-AU to en-GB, es-MX to es-419 then es, no to nb, pt-BR to pt then any pt), else
/// the next preferred language.
enum PreferredLocalization {
    static func first(from supported: [String], forPreferences preferred: [String]) -> String? {
        #if canImport(Darwin)
        return Bundle.preferredLocalizations(from: supported, forPreferences: preferred).first
        #else
        let names = Dictionary(supported.map { (key($0), $0) }, uniquingKeysWith: { a, _ in a })
        for tag in preferred {
            for candidate in chain(tag) { if let hit = names[candidate] { return hit } }
            // any variant of the language, as Apple's matcher gives pt-PT for pt-BR when there is no pt
            let lang = parts(tag).language
            if let hit = supported.first(where: { parts($0).language == lang }) { return hit }
        }
        return nil
        #endif
    }

    /// "zh_Hant-TW" -> "zh-hant-tw": the form both sides are compared in.
    static func key(_ tag: String) -> String { tag.replacingOccurrences(of: "_", with: "-").lowercased() }

    static func parts(_ tag: String) -> (language: String, script: String?, region: String?) {
        let p = key(tag).split(separator: "-").map(String.init)
        var language = p.first ?? ""
        language = ["no": "nb", "iw": "he", "in": "id", "ji": "yi"][language] ?? language
        var script: String?, region: String?
        for x in p.dropFirst() {
            if x.count == 4, script == nil { script = x } else if region == nil, x.count == 2 || x.count == 3 { region = x }
        }
        if language == "zh", script == nil {
            script = ["tw", "hk", "mo"].contains(region ?? "") ? "hant" : "hans"
        }
        return (language, script, region)
    }

    /// The tags to try for `tag`, most specific first.
    static func chain(_ tag: String) -> [String] {
        let (l, s, r) = parts(tag)
        var out: [String] = []
        func add(_ t: String) { if !out.contains(t) { out.append(t) } }
        if let s, let r { add("\(l)-\(s)-\(r)") }
        if let s { add("\(l)-\(s)") }
        if let r { add("\(l)-\(r)") }
        // Apple's parent locales: English outside the US goes to British English, Spanish in the Americas to
        // Latin American Spanish.
        if l == "en", let r, !["us", "as", "gu", "mp", "pr", "um", "vi"].contains(r) { add("en-gb") }
        if l == "es", let r, r != "es" { add("es-419") }
        add(l)
        return out
    }
}

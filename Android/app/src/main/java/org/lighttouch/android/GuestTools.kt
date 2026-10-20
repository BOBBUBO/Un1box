package org.lighttouch.android

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.zip.Inflater

/**
 * The guest tools shipped in the APK (assets/guest-tools/armv6.itpack and armv7.itpack). A new
 * device gets the pack with a family for its board and build, matched the way the library's
 * guest_offer matches it; firmware preparation takes its seven guest files from the armv6 pack.
 */
internal object GuestTools {
    private class Pack(val asset: String, val files: Map<String, ByteArray>, val families: List<JSONObject>)

    @Volatile private var cache: List<Pack>? = null

    private fun packs(context: Context): List<Pack> = cache ?: synchronized(this) {
        cache ?: (context.assets.list("guest-tools") ?: emptyArray()).filter { it.endsWith(".itpack") }.sorted().mapNotNull { name ->
            try { read("guest-tools/$name", context.assets.open("guest-tools/$name").use { it.readBytes() }) } catch (_: Exception) { null }
        }.also { cache = it }
    }

    /** ITPACK01: magic, a little-endian index length, the JSON index, then one zlib stream of every entry in order. */
    private fun read(asset: String, blob: ByteArray): Pack {
        require(blob.size > 12 && String(blob, 0, 8, Charsets.US_ASCII) == "ITPACK01") { "$asset is not a guest package" }
        val length = (blob[8].toInt() and 0xff) or ((blob[9].toInt() and 0xff) shl 8) or ((blob[10].toInt() and 0xff) shl 16) or ((blob[11].toInt() and 0xff) shl 24)
        val entries = JSONObject(String(blob, 12, length, Charsets.UTF_8)).getJSONArray("entries")
        val total = (0 until entries.length()).sumOf { entries.getJSONObject(it).getLong("size") }
        require(total in 1..(64L shl 20)) { "$asset exceeds the guest package limit" }
        val payload = ByteArray(total.toInt())
        Inflater().apply { setInput(blob, 12 + length, blob.size - 12 - length) }.run {
            var at = 0
            while (at < payload.size && !finished()) at += inflate(payload, at, payload.size - at).also { require(it > 0 || !needsInput()) { "$asset is truncated" } }
            end(); require(at == payload.size) { "$asset is truncated" }
        }
        var offset = 0
        val files = (0 until entries.length()).associate { i ->
            val entry = entries.getJSONObject(i); val size = entry.getInt("size")
            (entry.getString("name") to payload.copyOfRange(offset, offset + size)).also { offset += size }
        }
        val families = files.filterKeys { it.endsWith("/manifest.json") }.map { JSONObject(String(it.value, Charsets.UTF_8)) }
        return Pack(asset, files, families)
    }

    private fun JSONArray.strings() = (0 until length()).map { getString(it) }

    private fun covers(family: JSONObject, board: String, build: String): Boolean {
        if (family.optBoolean("stub")) return false
        val requires = family.getJSONObject("requires")
        val major = build.takeWhile { it.isDigit() }
        return board in requires.getJSONArray("boards").strings() &&
            requires.getJSONArray("builds").strings().any { it == build || it == "$major*" }
    }

    /** The bundled pack for [board]-[build], copied out of the APK so the library can import it; null when none covers it. */
    fun forDevice(context: Context, board: String, build: String): File? {
        val pack = packs(context).firstOrNull { pack -> pack.families.any { covers(it, board, build) } } ?: return null
        val target = File(context.filesDir, "bundled-${pack.asset.substringAfterLast('/')}")
        val bytes = context.assets.open(pack.asset).use { it.readBytes() }
        if (!target.isFile || !target.readBytes().contentEquals(bytes)) {
            val partial = File(target.path + ".partial"); partial.writeBytes(bytes)
            check(partial.renameTo(target)) { "Could not stage the built-in guest tools" }
        }
        return target
    }

    private fun serial(pack: Pack, board: String, build: String) = pack.families.firstOrNull { covers(it, board, build) }?.optInt("serial", 0)

    /**
     * The bundled pack, staged, when it is newer than the guest tools device [recordId] carries; null otherwise. A
     * device keeps the tools it was given, so after an update of LightTouch that bundles newer ones they are handed
     * over before the device starts, and iOS installs them during that boot. Never older: a newer pack the user
     * chose stays, and so does one this build cannot read.
     */
    fun newerFor(context: Context, recordId: String): File? {
        val record = File(context.filesDir, "library/records/$recordId")
        val metadata = JSONObject(File(record, "record.json").readText())
        val board = metadata.optString("board"); val build = metadata.optString("build")
        val bundled = packs(context).firstOrNull { pack -> pack.families.any { covers(it, board, build) } } ?: return null
        val installed = File(record, "assets/guest.itpack")
        val have = if (!installed.isFile) 0 else try { serial(read("guest.itpack", installed.readBytes()), board, build) ?: 0 } catch (_: Exception) { return null }
        return if ((serial(bundled, board, build) ?: 0) > have) forDevice(context, board, build) else null
    }

    /**
     * The tools folder the firmware preparer reads: release-guest-unpacked/guest-tools/ holding the
     * iPod touch 2G family's guest binaries and armv6.itpack, plus an inventory.json of their
     * SHA-256 digests. Rebuilt from the APK each time, so it always matches the bundled pack.
     */
    fun preparerRoot(context: Context): File {
        val pack = packs(context).firstOrNull { it.asset.endsWith("/armv6.itpack") } ?: error("This build of LightTouch has no built-in guest tools")
        val family = pack.families.firstOrNull { covers(it, "n72ap", "7E18") } ?: error("The built-in guest tools do not cover the iPod touch 2G")
        val prefix = family.getString("family") + "/"
        val wanted = mapOf("OpenGLES" to "hooks/OpenGLES", "libappsync.dylib" to "hooks/libappsync.dylib", "it_typein.dylib" to "hooks/it_typein.dylib",
            "it_agent" to "bin/it_agent", "sblaunch" to "bin/sblaunch", "sbdlicon" to "bin/sbdlicon")
        val root = File(context.filesDir, "preparer-tools")
        root.deleteRecursively()
        val tools = File(root, "release-guest-unpacked/guest-tools").apply { check(mkdirs()) { "Could not create the preparation tools folder" } }
        val inventory = JSONArray()
        fun put(name: String, bytes: ByteArray) {
            File(tools, name).writeBytes(bytes)
            inventory.put(JSONObject().put("path", "release-guest-unpacked/guest-tools/$name").put("type", "file").put("sha256", sha256(bytes)))
        }
        for ((name, path) in wanted) put(name, pack.files[prefix + path] ?: error("The built-in guest tools lack $path"))
        put("armv6.itpack", context.assets.open(pack.asset).use { it.readBytes() })
        File(root, "inventory.json").writeText(JSONObject().put("schema", 1).put("source", "LightTouch APK").put("entries", inventory).toString(2))
        return root
    }

    private fun sha256(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
}

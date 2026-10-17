package org.lighttouch.android

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.LruCache
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.security.MessageDigest
import javax.net.ssl.HttpsURLConnection

/** The guest a search or download is checked against: what it runs, not the Android host. */
data class StoreTarget(val productType: String, val ios: String, val arch: String, val family: String = "1") {
    companion object {
        private val armv6 = setOf("iPod1,1", "iPod2,1", "iPhone1,1", "iPhone1,2")
        fun of(productType: String, ios: String) =
            StoreTarget(productType, ios, if (productType in armv6) "armv6" else "armv7", if (productType.startsWith("iPad")) "2" else "1")
    }
}

class StoreError(message: String) : Exception(message)

/** One archived copy of one version, as the version picker lists it. */
data class StoreCopy(val ipaId: Int, val version: String, val size: Long?, val minimumOS: String?)

/**
 * Legacy Store (legacystore.app) client: a port of Desktop/app_services/catalog.py and
 * LightTouchCore's CatalogClient/CatalogCopy. Nothing is downloaded until download() is
 * called, and a copy is rechecked against the target immediately before transfer.
 * MD5 checks archive integrity, not publisher authenticity.
 */
object LegacyStore {
    private const val BASE = "https://legacystore.app"
    private const val USER_AGENT = "LightTouchAndroid/0.1 (+https://github.com/blake-lucas/LightTouch)"
    private const val JSON_LIMIT = 8 shl 20
    private const val DOWNLOAD_LIMIT = 2L shl 30

    fun version(value: String?): List<Int>? {
        if (value == null || !Regex("[0-9]+(\\.[0-9]+){0,2}").matches(value)) return null
        val parts = value.split('.').map { it.toInt() }
        return if (parts[0] == 0) null else parts + List(3 - parts.size) { 0 }
    }
    fun newer(a: List<Int>, b: List<Int>) = a.zip(b).firstOrNull { it.first != it.second }?.let { it.first > it.second } ?: false
    private fun requires(minimum: String?, target: StoreTarget): Boolean {
        if (minimum == null) return false
        val need = version(minimum) ?: throw StoreError("The minimum iOS version could not be verified.")
        return newer(need, version(target.ios) ?: throw StoreError("Invalid guest iOS version."))
    }

    /** Why a search row cannot be installed on the target, or null. Mirrors catalog.py's wording. */
    fun incompatibility(app: JSONObject): String? {
        val compat = app.optJSONObject("compat") ?: return null
        if (compat.optBoolean("compatible", true)) return null
        val reason = compat.optJSONArray("reasons")?.optString(0) ?: ""
        return when {
            reason == "armv6_slice_contains_armv7_code" || (reason.startsWith("no_") && reason.endsWith("_slice")) -> "Needs a newer processor"
            reason.startsWith("requires_ios_") -> "Requires iOS " + reason.removePrefix("requires_ios_")
            reason.startsWith("capability:!") || reason.endsWith("device_family") || reason == "family_not_requested" -> "Not made for this device"
            reason.startsWith("capability:") -> "Needs hardware this device does not have"
            reason == "encrypted" -> "Encrypted — cannot open in LightTouch"
            reason == "unavailable" -> "Download no longer available"
            reason == "not_analyzed" -> "Not checked for compatibility yet"
            else -> "Not compatible with this device"
        }
    }

    private fun open(url: String): HttpURLConnection {
        val parsed = URL(url)
        if (parsed.protocol != "https" || parsed.host.isNullOrEmpty() || parsed.userInfo != null) throw StoreError("The catalog supplied an invalid HTTPS address.")
        val connection = parsed.openConnection() as HttpsURLConnection
        connection.connectTimeout = 30_000; connection.readTimeout = 30_000
        connection.setRequestProperty("User-Agent", USER_AGENT)
        // HttpURLConnection never follows a redirect across protocols, so an https
        // request cannot be downgraded; the final URL is checked again below.
        val code = try { connection.responseCode } catch (e: java.io.IOException) {
            throw StoreError("Could not connect to Legacy Store: ${e.message}")
        }
        if (connection.url.protocol != "https") { connection.disconnect(); throw StoreError("The catalog redirected to an insecure address.") }
        if (code != 200) {
            val body = connection.errorStream?.use { it.readNBytes(4096) }?.decodeToString() ?: ""
            connection.disconnect()
            throw StoreError(when {
                code == 400 && "device must be one of" in body -> "Legacy Store does not support this device yet."
                code == 503 -> "The Internet Archive is busy; try again later."
                else -> "Legacy Store could not answer the request (HTTP $code)."
            })
        }
        return connection
    }

    private fun json(path: String, query: Map<String, String> = emptyMap()): JSONObject {
        val url = BASE + path + if (query.isEmpty()) "" else "?" + query.entries.joinToString("&") {
            URLEncoder.encode(it.key, "UTF-8") + "=" + URLEncoder.encode(it.value, "UTF-8")
        }
        val connection = open(url)
        val body = try { connection.inputStream.use { it.readNBytes(JSON_LIMIT + 1) } } finally { connection.disconnect() }
        if (body.size > JSON_LIMIT) throw StoreError("The catalog response exceeds the size limit.")
        return try { JSONObject(body.decodeToString()) } catch (_: org.json.JSONException) { throw StoreError("Legacy Store sent an unreadable response.") }
    }

    private fun apps(query: Map<String, String>, target: StoreTarget): List<JSONObject> {
        val list = json("/api/emulator/apps", query + mapOf("device" to target.productType, "os" to target.ios)).optJSONArray("apps")
            ?: throw StoreError("Legacy Store sent an unreadable app list.")
        if (list.length() > 500) throw StoreError("Legacy Store sent an unreadable app list.")
        return (0 until list.length()).map { index ->
            val app = list.optJSONObject(index)
            if (app == null || app.optInt("ipa_id") <= 0 || app.optString("name").isEmpty() || app.optString("download_url").isEmpty())
                throw StoreError("Legacy Store sent an unreadable app entry.")
            app
        }
    }

    /** Empty query lists the catalog's featured compatible apps; a query includes incompatible rows. */
    fun search(query: String, target: StoreTarget): List<JSONObject> =
        apps(if (query.isBlank()) mapOf("limit" to "50") else mapOf("limit" to "50", "q" to query.trim(), "incompatible" to "include"), target)

    fun compatibleCopy(ipaId: Int, target: StoreTarget): JSONObject {
        val found = apps(mapOf("ipa_id" to ipaId.toString()), target)
        if (found.size != 1 || found[0].optInt("ipa_id") != ipaId) throw StoreError("This copy is no longer available.")
        incompatibility(found[0])?.let { throw StoreError(it) }
        return found[0]
    }

    fun copyDetails(ipaId: Int): JSONObject {
        val copy = json("/api/v1/copies/$ipaId")
        if (copy.optString("ipa_id") != ipaId.toString()) throw StoreError("Legacy Store returned a different archived copy.")
        return copy
    }

    private fun runs(architectures: JSONArray?, target: StoreTarget): Boolean =
        architectures != null && (0 until architectures.length()).map { architectures.optString(it) }
            .any { it == target.arch || (target.arch == "armv7" && it == "armv6") }

    /**
     * Every archived copy of the app that could run on the target, newest first. The
     * copy the search row named is always kept so the picker can show it selected.
     */
    fun versions(app: JSONObject, target: StoreTarget): List<StoreCopy> {
        val key = app.optString("bundle_id").ifEmpty { app.optString("app_url").trimEnd('/').substringAfterLast('/') }
        if (key.isEmpty()) throw StoreError("This app has no catalog identifier.")
        val data = json("/api/v1/apps/${URLEncoder.encode(key, "UTF-8").replace("+", "%20")}/versions").optJSONArray("data")
            ?: throw StoreError("Legacy Store sent unreadable version information.")
        val chosen = app.optInt("ipa_id")
        val rows = mutableListOf<StoreCopy>()
        for (v in 0 until data.length()) {
            val entry = data.optJSONObject(v) ?: continue
            val minimum = entry.optString("minimum_os_version").ifEmpty { null }
            val copies = entry.optJSONArray("copies") ?: continue
            for (c in 0 until copies.length()) {
                val copy = copies.optJSONObject(c) ?: continue
                val id = copy.optString("ipa_id").toIntOrNull() ?: continue
                val machoMinimum = copy.optString("macho_min_os").ifEmpty { null }
                val fits = copy.optString("install_status") == "installable" && runs(copy.optJSONArray("architectures"), target) &&
                    runCatching { !requires(minimum, target) && !requires(machoMinimum, target) }.getOrDefault(false)
                if (fits || id == chosen) rows += StoreCopy(id, entry.optString("version", "Unknown"),
                    copy.optLong("size").takeIf { it > 0 }, listOfNotNull(minimum, machoMinimum).maxWithOrNull { a, b ->
                        val x = version(a) ?: listOf(0, 0, 0); val y = version(b) ?: listOf(0, 0, 0)
                        if (newer(x, y)) 1 else if (newer(y, x)) -1 else 0 })
            }
        }
        return rows
    }

    /** catalog.py check_copy: identity, availability, slice, family and minimum OS. */
    fun checkCopy(copy: JSONObject, app: JSONObject, target: StoreTarget) {
        if (copy.optString("ipa_id") != app.optInt("ipa_id").toString() || copy.optString("bundle_id") != app.optString("bundle_id"))
            throw StoreError("The archived copy no longer matches this app.")
        if (!copy.optBoolean("available", false)) throw StoreError("This archived download is no longer available.")
        val binary = copy.optJSONObject("binary") ?: throw StoreError("This copy has not been analyzed for compatibility.")
        if (binary.optString("install_status") != "installable") throw StoreError("This copy is encrypted or has not been classified as installable.")
        if (!runs(binary.optJSONArray("architectures"), target) || (target.arch == "armv6" && binary.optBoolean("armv7_code", false)))
            throw StoreError("This copy needs a newer processor than this device has.")
        binary.optJSONArray("device_family_macho")?.let { families ->
            if (families.length() > 0 && (0 until families.length()).none { families.optString(it) == target.family })
                throw StoreError("This copy is not made for this device.")
        }
        for (minimum in listOf(app.optString("min_os"), binary.optString("macho_min_os")).filter { it.isNotEmpty() && it != "null" })
            if (requires(minimum, target)) throw StoreError("Requires iOS $minimum; this device runs iOS ${target.ios}.")
    }

    /** Download one revalidated copy into a fresh private directory; the caller deletes it after install. */
    fun download(app: JSONObject, target: StoreTarget, scratch: File, progress: (Long, Long?) -> Unit, cancelled: () -> Boolean): File {
        val current = compatibleCopy(app.optInt("ipa_id"), target)
        if (current.optString("bundle_id") != app.optString("bundle_id")) throw StoreError("The archived copy no longer matches this app.")
        val details = copyDetails(current.optInt("ipa_id"))
        checkCopy(details, current, target)
        val checksum = details.optString("md5").takeIf { it.isNotEmpty() && it != "null" }
        if (checksum != null && !Regex("[0-9a-fA-F]{32}").matches(checksum)) throw StoreError("The archive supplied an invalid file checksum.")
        val size = if (details.isNull("size")) null else details.optLong("size").also {
            if (it !in 1..DOWNLOAD_LIMIT) throw StoreError("The archive supplied an invalid file size.")
        }
        val directory = File(scratch, "catalog-${current.optInt("ipa_id")}-${System.nanoTime()}")
        if (!directory.mkdirs()) throw StoreError("Could not create a download folder.")
        val partial = File(directory, "download.partial"); val final = File(directory, "App.ipa")
        try {
            val connection = open(current.optString("download_url"))
            val digest = MessageDigest.getInstance("MD5")
            var count = 0L
            try {
                connection.inputStream.use { input: InputStream -> partial.outputStream().use { output ->
                    val buffer = ByteArray(1 shl 16)
                    while (true) {
                        if (cancelled()) throw StoreError("Download cancelled.")
                        val read = input.read(buffer)
                        if (read < 0) break
                        count += read
                        if (count > DOWNLOAD_LIMIT || (size != null && count > size)) throw StoreError("The download exceeds its expected size.")
                        output.write(buffer, 0, read); digest.update(buffer, 0, read)
                        progress(count, size)
                    }
                    output.fd.sync()
                } }
            } finally { connection.disconnect() }
            if (count == 0L || (size != null && count != size)) throw StoreError("The download is incomplete or differs from the archive size.")
            if (checksum != null && digest.digest().joinToString("") { "%02x".format(it) } != checksum.lowercase())
                throw StoreError("The downloaded IPA failed its checksum check.")
            if (!partial.renameTo(final)) throw StoreError("Could not publish the download.")
            return final
        } catch (e: Exception) {
            directory.deleteRecursively()
            throw if (e is StoreError) e else StoreError("Download failed: ${e.message}")
        }
    }

    private val icons = LruCache<String, Bitmap>(128)
    /** Small app icons for the result list; failures just leave the row without one. */
    fun icon(url: String): Bitmap? {
        icons.get(url)?.let { return it }
        return runCatching {
            val connection = open(url)
            try { connection.inputStream.use { BitmapFactory.decodeStream(it) } } finally { connection.disconnect() }
        }.getOrNull()?.also { icons.put(url, it) }
    }
}

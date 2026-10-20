package org.lighttouch.android

import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import org.json.JSONObject
import java.io.File
import java.io.RandomAccessFile
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/** One firmware of the bundled catalog (Portable/firmware/firmware-catalog.json). */
internal data class CatalogFirmware(
    val id: String, val board: String, val productType: String, val version: String, val build: String,
    val released: String, val prerelease: String, val sha1: String, val bytes: Long, val urls: List<String>,
) {
    /** "3.1.3", or "4.2 beta 2" for a developer build. */
    val label get() = if (prerelease.isEmpty()) version else "$version $prerelease"
    val fileName get() = "${productType}_${version}_${build}_Restore.ipsw"
}

internal object FirmwareCatalog {
    fun load(context: Context): List<CatalogFirmware> =
        parse(context.assets.open("firmware-catalog.json").bufferedReader().use { it.readText() })

    fun parse(text: String): List<CatalogFirmware> {
        val entries = JSONObject(text).getJSONArray("entries")
        return (0 until entries.length()).map { entries.getJSONObject(it) }.filter { it.has("product_type") && it.has("version") }.map { e ->
            val source = e.optJSONObject("source")
            val urls = source?.takeIf { it.optString("kind") == "ipsw" }?.let { s ->
                listOf(s.optString("url")) + (s.optJSONArray("mirrors")?.let { m -> (0 until m.length()).map { m.getJSONObject(it).optString("url") } } ?: emptyList())
            }?.filter { it.startsWith("https://") } ?: emptyList()
            val prerelease = e.optString("prerelease").let { kind -> if (kind.isEmpty()) "" else "$kind ${e.optInt("prerelease_number", 0).takeIf { it > 0 } ?: ""}".trim() }
            CatalogFirmware(e.getString("id"), e.getString("board"), e.getString("product_type"), e.getString("version"), e.getString("build"),
                e.optString("released"), prerelease, source?.optString("sha1") ?: "", source?.optLong("bytes") ?: 0L, urls)
        }
    }
}

/** Whether a device needs its boot ROM to start (the iPod touch 2G does; it is not in the IPSW). */
internal fun needsBootrom(board: String) = board == "n72ap"

/**
 * The iPod touch 2G's SecureROM (S5L8720, iBoot-240.4) is mask ROM in the chip, not part of any IPSW.
 * securerom.fun publishes dumps, from the GitHub repository behind it when the site is down; this
 * one is byte-identical to upstream LightTouchMac's bootrom_240_4, and a download must match it.
 */
internal object BootromDownload {
    private const val PATH_240_4 = "resources/APROM/SecureROM%20for%20s5l8720xsi%2C%20iBoot-240.4"
    private val links = listOf("https://securerom.fun/$PATH_240_4", "https://github.com/hekapooios/hekapooios.github.io/raw/master/$PATH_240_4")
    private const val SHA256_240_4 = "55f4d8ea2791ba51dd89934168f38f0fb21ce8762ff614c1e742407c0d3ca054"

    /** Downloads the boot ROM to [target]; blocking, so call it off the main thread. */
    fun fetch(target: File) {
        var failure = ""
        for (link in links) {
            try {
                val connection = URL(link).openConnection() as HttpURLConnection
                connection.connectTimeout = 15_000; connection.readTimeout = 30_000
                val bytes = try {
                    check(connection.responseCode == 200) { "HTTP ${connection.responseCode}" }
                    connection.inputStream.use { it.readBytes() }
                } finally { connection.disconnect() }
                val digest = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
                check(digest == SHA256_240_4) { "the file did not match" }
                val partial = File(target.path + ".partial"); partial.writeBytes(bytes)
                check(partial.renameTo(target)) { "could not save it" }
                return
            } catch (e: Exception) { failure = "${URL(link).host}: ${e.message}" }
        }
        error(failure)
    }
}

/**
 * Downloads a stock IPSW: the link ipsw.me (api.ipsw.me/v4) lists for the build, then the catalog's
 * own links, then the Internet Archive's copy. The file must match the catalog's SHA-1 and size. A
 * partial download resumes where it stopped. Lives outside the activity so a download survives the
 * activity being recreated.
 */
internal object IpswDownload {
    var firmwareId by mutableStateOf("")
        private set
    var progress by mutableFloatStateOf(-1f)
        private set
    var running by mutableStateOf(false)
        private set
    var status by mutableStateOf("")
        private set
    @Volatile private var cancelled = false

    fun directory(context: Context) = File(context.filesDir, "ipsw")

    fun cancel() { cancelled = true }

    /** Starts downloading [firmware]; [onDone] runs on the download thread with the verified file, or null and an error. */
    fun start(context: Context, firmware: CatalogFirmware, onDone: (File?, String) -> Unit) {
        if (running) return
        running = true; cancelled = false; firmwareId = firmware.id; progress = -1f; status = "Looking up ${firmware.fileName}"
        Thread({
            val result = try { download(context.applicationContext, firmware) to "" } catch (e: Exception) { null to (e.message ?: "Download failed") }
            running = false
            status = if (result.first != null) "" else result.second
            onDone(result.first, result.second)
        }, "ipsw-download").start()
    }

    /** Asks ipsw.me for the build's link; it lists Apple's own servers, and lacks some builds (iPod touch 2G 3.x). */
    private fun ipswMe(firmware: CatalogFirmware): String? = try {
        val connection = URL("https://api.ipsw.me/v4/ipsw/${firmware.productType}/${firmware.build}").openConnection() as HttpURLConnection
        connection.connectTimeout = 10_000; connection.readTimeout = 10_000
        connection.setRequestProperty("Accept", "application/json")
        try {
            if (connection.responseCode != 200) null
            else JSONObject(connection.inputStream.bufferedReader().use { it.readText() }).let { info ->
                info.optString("url").takeIf { it.startsWith("https://") && (firmware.sha1.isEmpty() || info.optString("sha1sum").equals(firmware.sha1, true)) }
            }
        } finally { connection.disconnect() }
    } catch (_: Exception) { null }

    private fun download(context: Context, firmware: CatalogFirmware): File {
        val target = File(directory(context).apply { mkdirs() }, firmware.fileName)
        if (target.isFile && target.length() == firmware.bytes && sha1(target) == firmware.sha1) return target
        val partial = File(target.path + ".partial")
        // The Internet Archive's iOS Firmware Exhibit Collection keeps every IPSW under its Apple file name.
        val archive = "https://archive.org/download/iOS_Firmware_Exhibit_Collection/" + firmware.fileName.replace(",", "%2C")
        val links = (listOfNotNull(ipswMe(firmware)) + firmware.urls + archive).distinctBy { it.replace("%2C", ",") }
        require(links.isNotEmpty()) { "No download is known for iOS ${firmware.label}; choose an IPSW you have instead" }
        var failure = ""
        for (link in links) {
            check(!cancelled) { "Download cancelled" }
            try {
                fetch(link, partial, firmware.bytes)
                status = "Checking the download"
                val digest = sha1(partial)
                if (firmware.sha1.isNotEmpty() && digest != firmware.sha1) { partial.delete(); failure = "The download from ${URL(link).host} did not match"; continue }
                check(partial.renameTo(target)) { "Could not save the IPSW" }
                return target
            } catch (e: Exception) {
                if (cancelled) throw IllegalStateException("Download cancelled")
                failure = "${URL(link).host}: ${e.message}"
            }
        }
        error("Could not download iOS ${firmware.label} ($failure)")
    }

    private fun fetch(link: String, partial: File, expected: Long) {
        var have = if (partial.isFile) partial.length() else 0L
        if (expected > 0 && have > expected) { partial.delete(); have = 0 }
        val connection = URL(link).openConnection() as HttpURLConnection
        connection.connectTimeout = 15_000; connection.readTimeout = 30_000; connection.instanceFollowRedirects = true
        if (have > 0) connection.setRequestProperty("Range", "bytes=$have-")
        try {
            val code = connection.responseCode
            if (code == 416 && have == expected) return
            check(code == 200 || code == 206) { "HTTP $code" }
            if (code == 200) have = 0  // The server ignored the range; start over.
            val total = if (expected > 0) expected else have + connection.contentLengthLong
            status = "Downloading from ${URL(link).host}"
            RandomAccessFile(partial, "rw").use { out ->
                out.setLength(have); out.seek(have)
                connection.inputStream.use { input ->
                    val buffer = ByteArray(1 shl 16)
                    while (true) {
                        check(!cancelled) { "Download cancelled" }
                        val n = input.read(buffer); if (n < 0) break
                        out.write(buffer, 0, n); have += n
                        if (total > 0) progress = (have.toFloat() / total).coerceIn(0f, 1f)
                    }
                }
            }
            check(expected <= 0 || have == expected) { "the download stopped early" }
        } finally { connection.disconnect() }
    }

    fun sha1(file: File): String {
        val digest = MessageDigest.getInstance("SHA-1")
        file.inputStream().use { input -> val buffer = ByteArray(1 shl 20); while (true) { val n = input.read(buffer); if (n < 0) break; digest.update(buffer, 0, n) } }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}

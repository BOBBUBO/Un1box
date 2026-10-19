package org.lighttouch.android

import android.os.Build
import android.os.Bundle
import android.os.Process
import android.os.SystemClock
import org.json.JSONObject
import java.io.File
import java.io.RandomAccessFile
import java.util.UUID

/** Worker-owned captures survive Activity recreation and display reattachment. */
internal class ProfileCapture(private val context: android.content.Context, private val observations: () -> Bundle) {
    private val filesDir = context.filesDir
    private var directory: File? = null
    private var metadata: JSONObject? = null
    private var engineLog: File? = null
    private var logOffset = 0L
    private var notice = ""
    private var lastError = ""

    private fun encode(bundle: Bundle): JSONObject = JSONObject().apply {
        for (key in bundle.keySet()) {
            val value = bundle.get(key)
            put(key, if (value is Bundle) encode(value) else JSONObject.wrap(value))
        }
    }

    @Synchronized fun snapshot(guestState: String): Bundle {
        if (directory != null && SystemClock.elapsedRealtimeNanos() - (metadata?.optLong("startedElapsedRealtimeNs") ?: 0) >= 900_000_000_000L) finish("duration-limit")
        if (directory != null && (guestState !in listOf("running", "paused") || Native.profileStatus() != 1))
            finish(if (guestState in listOf("running", "paused")) "capture-limit-or-error" else "guest-$guestState")
        return Bundle().apply {
            putBoolean("profileRecording", directory != null)
            putString("profileNotice", notice)
            putString("profileError", lastError)
        }
    }

    @Synchronized fun change(recording: Boolean, guestState: String): Bundle {
        lastError = ""
        try {
            if (recording && directory == null) {
                check(guestState == "running") { "Start profiling while the guest is running" }
                // Resolve the identity before starting the timed capture. Keep the raw
                // response as evidence, including the localized display name.
                val request = Native.guestRequest(1, "", 2000)
                check(request[1].isEmpty()) { request[1] }
                val id = request[0].toLong()
                var reply: Array<String>
                try {
                    val deadline = SystemClock.elapsedRealtime() + 2500
                    do {
                        reply = Native.guestPoll(id)
                        check(reply[3].isEmpty()) { reply[3] }
                        if (reply[0] != "1") break
                        check(SystemClock.elapsedRealtime() < deadline) { "Could not identify the current iOS app" }
                        Thread.sleep(25)
                    } while (true)
                    check(reply[0] == "2" && reply[1] == "0") { "Could not identify the current iOS app" }
                } finally { Native.guestCancel(id) }
                val identity = reply[2].trimEnd().lines()
                check(identity.size >= 2 && identity[0].isNotBlank() && identity[1].isNotBlank()) {
                    "The guest returned an incomplete app identity"
                }
                val root = File(filesDir, "profiles").apply { mkdirs() }
                val dir = File(root, "${System.currentTimeMillis()}-${UUID.randomUUID().toString().take(8)}")
                check(dir.mkdir()) { "Could not create profile directory" }
                val log = Native.status().getOrNull(3)?.takeIf { it.isNotBlank() }?.let { File(it) }
                val info = JSONObject().apply {
                    put("format", 1); put("bundleId", identity[0]); put("appName", identity[1])
                    put("frontmostResponse", reply[2]); put("startedAtMs", System.currentTimeMillis())
                    put("startedElapsedRealtimeNs", SystemClock.elapsedRealtimeNanos())
                    put("startedMonotonicNs", System.nanoTime())
                    put("manufacturer", Build.MANUFACTURER); put("model", Build.MODEL)
                    put("hardware", Build.HARDWARE); put("androidBuild", Build.FINGERPRINT)
                    put("lightTouchVersionCode", BuildConfig.VERSION_CODE)
                    put("lightTouchVersion", BuildConfig.VERSION_NAME); put("workerPid", Process.myPid())
                    put("engineLog", log?.absolutePath ?: "")
                    put("instructionAccounting", System.getenv("IT_VCPU_ACCT") != null)
                    put("environment", JSONObject().apply {
                        System.getenv().filterKeys { it.startsWith("LT_") || it.startsWith("IT_GLES_") || it == "IT_VCPU_ACCT" }
                            .forEach { (key, value) -> put(key, value) }
                    })
                    put("state", "recording")
                    put("observationsAtStart", encode(observations()))
                }
                // Preserve build and engine identity manifests shipped with this APK.
                context.assets.list("licenses")?.filter { it != "firmware" }?.forEach { group ->
                    try { context.assets.open("licenses/$group/manifest.json").use { input ->
                        File(dir, "engine-$group-manifest.json").outputStream().use { input.copyTo(it) }
                    } } catch (_: java.io.IOException) { /* Older APK notice layouts may differ. */ }
                }
                log?.takeIf { it.exists() }?.inputStream()?.use { input ->
                    File(dir, "engine-start.log").outputStream().use { output ->
                        val buffer = ByteArray(8192)
                        var remaining = 65536
                        while (remaining > 0) {
                            val n = input.read(buffer, 0, minOf(buffer.size, remaining))
                            if (n <= 0) break
                            output.write(buffer, 0, n); remaining -= n
                        }
                    }
                }
                File(dir, "metadata.json").writeText(info.toString(2))
                val result = Native.profile(File(dir, "events.tsv").absolutePath)
                if (result.isNotEmpty()) {
                    info.put("state", "start-failed").put("error", result)
                    File(dir, "metadata.json").writeText(info.toString(2))
                    error(result)
                }
                directory = dir; metadata = info; engineLog = log; logOffset = log?.length() ?: 0L
                notice = "Profiling ${identity[1]}"
            } else if (!recording) finish("user")
        } catch (e: Exception) {
            lastError = e.message ?: "Profile capture failed"
        }
        return Bundle().apply {
            putBoolean("profileRecording", directory != null)
            putString("profileNotice", notice); putString("profileError", lastError)
        }
    }

    @Synchronized fun finish(reason: String) {
        val dir = directory ?: return
        try {
            val result = Native.profile("")
            if (result.isNotEmpty()) lastError = result
            engineLog?.takeIf { it.exists() }?.let { source ->
                RandomAccessFile(source, "r").use { input ->
                    input.seek(logOffset.coerceAtMost(input.length()))
                    File(dir, "engine.log").outputStream().use { output ->
                        val buffer = ByteArray(32768)
                        var remaining = (input.length() - input.filePointer).coerceAtMost(8L * 1024 * 1024)
                        while (remaining > 0) {
                            val n = input.read(buffer, 0, minOf(buffer.size.toLong(), remaining).toInt())
                            if (n <= 0) break
                            output.write(buffer, 0, n); remaining -= n
                        }
                    }
                }
            }
            metadata?.apply {
                put("state", if (lastError.isEmpty()) "saved" else "partial")
                put("stopReason", reason); put("endedAtMs", System.currentTimeMillis())
                put("endedElapsedRealtimeNs", SystemClock.elapsedRealtimeNanos())
                put("endedMonotonicNs", System.nanoTime()); put("error", lastError)
                put("observationsAtEnd", encode(observations()))
                File(dir, "metadata.json").writeText(toString(2))
            }
            notice = "Saved profile: ${metadata?.optString("appName") ?: "iOS app"}"
        } catch (e: Exception) {
            lastError = e.message ?: "Could not finish profile capture"
            notice = "Profile retained with incomplete metadata"
        } finally { directory = null; metadata = null; engineLog = null }
    }
}

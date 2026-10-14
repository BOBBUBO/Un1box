package org.lighttouch.android

import android.content.Intent
import android.os.Bundle
import android.os.Process
import android.os.SystemClock
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.ByteArrayOutputStream
import java.io.File
import java.security.MessageDigest
import java.util.zip.ZipFile

/** A real normal-app-UID provider exercise; no arbitrary environment or library path. */
@RunWith(AndroidJUnit4::class)
class GuestAngleProviderTest {
    @Test fun fixedPackagedProviderStartsInFreshGuestWorker() {
        val arguments = InstrumentationRegistry.getArguments()
        assumeTrue("Explicit packaged ANGLE guest opt-in required", arguments.getString("exerciseAngleGuest") == "true")
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val evidence = File(context.filesDir, "angle-evidence-${System.currentTimeMillis()}").apply { check(mkdir()) }
        val receipt = JSONObject().put("providerDispatchObserved", false).put("guestAcceptancePassed", false)
            .put("visibleGuestVerified", false).put("audiblePlaybackVerified", false)
        val timeline = JSONArray()
        @Suppress("DEPRECATION")
        fun encode(bundle: Bundle): JSONObject = JSONObject().apply {
            for (key in bundle.keySet()) when (val value = bundle.get(key)) {
                is Bundle -> put(key, encode(value))
                is LongArray -> put(key, JSONArray(value.toList()))
                is IntArray -> put(key, JSONArray(value.toList()))
                else -> put(key, value)
            }
        }
        fun persist() {
            File(evidence, "result.json").writeText(receipt.toString(2))
            File(evidence, "observations.json").writeText(timeline.toString(2))
        }
        try {
            assertEquals("Only the explicitly packaged x86 experiment is accepted", "x86_64", android.os.Build.SUPPORTED_ABIS[0])
            val manifest = JSONObject(context.assets.open("providers/angle-runtime.json").bufferedReader().use { it.readText() })
            receipt.put("runtimeManifest", manifest).put("installedApkSha256", acceptanceSha256(File(context.applicationInfo.sourceDir)))
            ZipFile(context.applicationInfo.sourceDir).use { apk ->
                val libraries = manifest.getJSONObject("libraries")
                for (name in listOf("libEGL_lighttouch_angle.so", "libGLESv1_CM_lighttouch_angle.so", "libGLESv2_lighttouch_angle.so")) {
                    val entry = requireNotNull(apk.getEntry("lib/x86_64/$name"))
                    val digest = MessageDigest.getInstance("SHA-256")
                    apk.getInputStream(entry).use { input -> val bytes = ByteArray(65536); while (true) { val n = input.read(bytes); if (n < 0) break; digest.update(bytes, 0, n) } }
                    assertEquals("Packaged provider identity", libraries.getJSONObject(name).getString("sha256"), digest.digest().joinToString("") { "%02x".format(it) })
                }
            }
            ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                AcceptanceBinding(context, Intent(context, EngineService::class.java), IEngine.Stub::asInterface).use { binding ->
                    val worker = binding.get()
                    val initial = worker.status()
                    assertEquals(Process.myUid(), initial.getInt("uid")); assertNotEquals(Process.myPid(), initial.getInt("pid"))
                    assertFalse("Root must dispose any consumed worker first", initial.getBoolean("sessionStarted"))
                    assertEquals("stopped", initial.getString("state"))
                    assertTrue("Unknown choice was accepted", worker.debugSelectGraphicsProvider(99).isNotEmpty())
                    assertEquals("Fixed provider library preflight", "", worker.debugSelectGraphicsProvider(1))
                    assertTrue("Provider choice must be immutable even before start", worker.debugSelectGraphicsProvider(0).isNotEmpty())
                    val selected = worker.status()
                    assertEquals("angle-gl", selected.getString("graphicsProviderChoice")); assertTrue(selected.getBoolean("graphicsProviderFixed"))
                    val record = arguments.getString("recordId") ?: requireNotNull(worker.records().firstOrNull { it.getBoolean("selected") }?.getString("id"))
                    scenario.onActivity { it.startForegroundService(Intent(it, EngineService::class.java)) }
                    val started = worker.start(record)
                    assertEquals(started.toString(), "", started.getString("error"))
                    assertTrue("Provider setter admitted after start", worker.debugSelectGraphicsProvider(1).isNotEmpty())
                    receipt.put("workerPid", started.getInt("pid")).put("appUid", Process.myUid()).put("recordId", record)
                    val log = File(requireNotNull(started.getString("engineLog")))
                    val seconds = arguments.getString("angleObserveSeconds", "180").toLong().coerceIn(10, 1200)
                    val deadline = SystemClock.elapsedRealtime() + seconds * 1000
                    val logPrefix = ByteArrayOutputStream()
                    fun prefix(): String {
                        // Read each retained byte at most once, including during
                        // slow guest boot; never scan unrelated/process-wide logs.
                        java.io.RandomAccessFile(log, "r").use { input ->
                            input.seek(logPrefix.size().toLong())
                            val bytes = ByteArray(8192)
                            while (logPrefix.size() < 4 * 1024 * 1024) {
                                val n = input.read(bytes, 0, minOf(bytes.size, 4 * 1024 * 1024 - logPrefix.size()))
                                if (n < 0) break
                                logPrefix.write(bytes, 0, n)
                            }
                        }
                        return logPrefix.toByteArray().toString(Charsets.UTF_8)
                    }
                    var providerLog = ""
                    do {
                        val state = worker.status()
                        assertEquals("Worker changed", started.getInt("pid"), state.getInt("pid"))
                        timeline.put(JSONObject().put("hostElapsedMs", SystemClock.elapsedRealtime()).put("status", encode(state))); persist()
                        check(state.getString("state") !in listOf("failed", "stopped")) { state.toString() }
                        providerLog = prefix()
                        if (providerLog.contains("[gles-egl] provider=angle-gl; verified public EGL/GLES dispatch")) break
                        Thread.sleep(2000)
                    } while (SystemClock.elapsedRealtime() < deadline)
                    File(evidence, "provider-log-prefix.txt").writeText(providerLog)
                    receipt.put("engineLog", log.absolutePath).put("logPrefixLimitBytes", 4 * 1024 * 1024)
                        .put("logPrefixTruncated", log.length() > 4 * 1024 * 1024).put("observationSeconds", seconds)
                    val ownerLines = providerLog.lineSequence().filter { it.startsWith("[gles-egl] public entry ") }.toList()
                    receipt.put("entryOwnerLines", JSONArray(ownerLines))
                    assertTrue("Actual engine did not confirm the fixed provider; old engine candidates are insufficient", providerLog.contains("[gles-egl] provider=angle-gl; verified public EGL/GLES dispatch"))
                    assertTrue("Actual public address-owner checks missing", ownerLines.size >= 197)
                    val image = File(context.filesDir, "screenshots/${evidence.name}/provider.png")
                    val captureError = worker.screenshot(image.absolutePath)
                    receipt.put("capture", image.absolutePath).put("captureError", captureError)
                        .put("terminalWorker", encode(worker.status())).put("providerDispatchObserved", true)
                    assertEquals("Actual copied guest frame capture", "", captureError)
                    if (arguments.getString("requireNonzeroGuestFrame") == "true") assertTrue("Chosen guest frame sample stayed black", worker.status().getBundle("observations")!!.getLong("guestFrameNonzeroRgbSamples") > 0)
                }
            }
        } catch (failure: Throwable) { receipt.put("failure", failure.toString()); throw failure }
        finally { persist() }
    }
}

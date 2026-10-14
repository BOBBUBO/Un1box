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
import java.io.File

/** Overlay this file into an isolated matched test build, never the production APK. */
@RunWith(AndroidJUnit4::class)
class GuestPixelBootDiagnosisTest {
    @Test fun retainForegroundBootFramesAndCpuState() {
        val args = InstrumentationRegistry.getArguments()
        assumeTrue("Explicit virtual Pixel diagnosis required", args.getString("diagnoseVirtualPixel") == "true")
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        assertEquals("Fixed x86 virtual attempt", "x86_64", android.os.Build.SUPPORTED_ABIS[0])
        val expectedMainSha = requireNotNull(args.getString("expectedMainApkSha256"))
        require(Regex("[0-9a-f]{64}").matches(expectedMainSha)) { "Explicit sealed main APK SHA-256 required" }
        assertEquals("Installed production APK differs from sealed main artifact", expectedMainSha,
            acceptanceSha256(File(context.applicationInfo.sourceDir)))
        val record = requireNotNull(args.getString("recordId"))
        require(Regex("[0-9a-f]{32}").matches(record))
        val seconds = args.getString("pixelObserveSeconds", "300").toLong()
        require(seconds == 300L) { "The declared observation is exactly300 seconds; no shortening" }
        val token = requireNotNull(args.getString("pixelAttemptToken"))
        val originToken = requireNotNull(args.getString("pixelOriginToken"))
        require(Regex("[0-9a-f]{32}").matches(token))
        require(Regex("[0-9a-f]{64}").matches(originToken))
        val evidence = File(context.filesDir, "pixel-boot-$token").apply { check(mkdir()) }
        val timeline = JSONArray()
        val receipt = JSONObject().put("diagnosisCaptured", false).put("guestAcceptancePassed", false).put("visibleGuestVerified", false)
            .put("physicalPixelEquivalent", false).put("audiblePlaybackVerified", false)
            .put("recordId", record).put("observationSeconds", seconds).put("expectedMainApkSha256", expectedMainSha)
        @Suppress("DEPRECATION")
        fun encode(b: Bundle): JSONObject = JSONObject().apply {
            for (key in b.keySet()) when (val value = b.get(key)) {
                is Bundle -> put(key, encode(value))
                is IntArray -> put(key, JSONArray(value.toList()))
                is LongArray -> put(key, JSONArray(value.toList()))
                else -> put(key, value)
            }
        }
        fun atomic(name: String, data: String) {
            val pending = File(evidence, ".$name.pending")
            pending.writeText(data)
            check(pending.renameTo(File(evidence, name))) { "Atomic diagnostic publication failed" }
        }
        fun persist() {
            atomic("result.json", receipt.toString(2))
            atomic("timeline.json", timeline.toString(2))
        }
        try {
            ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                AcceptanceBinding(context, Intent(context, EngineService::class.java), IEngine.Stub::asInterface).use { binding ->
                    val worker = binding.get()
                    val initial = worker.status()
                    assertEquals(Process.myUid(), initial.getInt("uid"))
                    assertNotEquals(Process.myPid(), initial.getInt("pid"))
                    assertFalse("Fresh unused worker required", initial.getBoolean("sessionStarted"))
                    assertEquals("stopped", initial.getString("state"))
                    assertEquals("", worker.debugSelectGraphicsProvider(1))
                    assertEquals("", worker.debugArmQmp())
                    scenario.onActivity { it.startForegroundService(Intent(it, EngineService::class.java)) }
                    val beforeStartMs = SystemClock.elapsedRealtime()
                    val started = worker.start(record)
                    val afterStartMs = SystemClock.elapsedRealtime()
                    assertEquals(started.toString(), "", started.getString("error"))
                    val pid = started.getInt("pid")
                    assertEquals(initial.getInt("pid"), pid)
                    val log = File(requireNotNull(started.getString("engineLog")))
                    val bootId = File("/proc/sys/kernel/random/boot_id").readText().trim()
                    val stat = File("/proc/$pid/stat").readText().substringAfterLast(") ").trim().split(Regex("\\s+"))
                    val startTicks = stat[19].toLong()
                    val ready = JSONObject().put("schema", 1).put("workerPid", pid).put("appUid", Process.myUid())
                        .put("workerStartTicks", startTicks).put("bootId", bootId).put("recordId", record)
                        .put("attemptToken", token).put("originToken", originToken)
                        .put("beforeStartElapsedMs", beforeStartMs).put("afterStartElapsedMs", afterStartMs)
                        .put("nativeAfterStartMonotonicNs", started.getBundle("observations")?.getLong("hostMonotonicNs"))
                        .put("guestAcceptancePassed", false)
                    atomic("ready.json", ready.toString(2))
                    receipt.put("workerPid", pid).put("appUid", Process.myUid())
                        .put("engineLog", log.absolutePath).put("initialWorker", encode(started))
                        .put("workerStartTicks", startTicks).put("bootId", bootId)
                        .put("attemptToken", token).put("originToken", originToken)
                        .put("beforeStartElapsedMs", beforeStartMs).put("afterStartElapsedMs", afterStartMs)
                        .put("stage", "awaiting-host-after-start-admission")
                        .put("qmpMayPerturbTiming", true)
                    persist()
                    val gatePath = File(evidence, "admission.json")
                    val waitDeadline = SystemClock.elapsedRealtime() + 20_000
                    while (!gatePath.exists() && SystemClock.elapsedRealtime() < waitDeadline) Thread.sleep(100)
                    check(gatePath.exists()) { "No timely host after-Start admission; interval not shortened" }
                    val gate = JSONObject(gatePath.readText())
                    for (key in listOf("workerPid", "appUid", "workerStartTicks", "bootId", "recordId", "attemptToken", "originToken"))
                        check(gate.get(key).toString() == ready.get(key).toString()) { "Admission identity differs: $key" }
                    check(gate.getLong("observationMs") == 300_000L)
                    val observationStartMs = SystemClock.elapsedRealtime()
                    val absoluteLimitMs = gate.getLong("deviceObservationLimitMs")
                    check(absoluteLimitMs - observationStartMs >= 300_000L) { "Insufficient original device budget after Start/admission" }
                    val deadline = observationStartMs + 300_000L
                    receipt.put("observationStartedElapsedMs", observationStartMs)
                        .put("observationDeadlineElapsedMs", deadline).put("deviceOriginalObservationLimitMs", absoluteLimitMs)
                        .put("hostClockAdmission", gate).put("stage", "foreground-observation")
                    persist()
                    var index = 0
                    var wokeOnce = false
                    while (SystemClock.elapsedRealtime() < deadline) {
                        val state = worker.status()
                        assertEquals("Worker replaced during diagnosis", pid, state.getInt("pid"))
                        assertTrue("Unexpected pause/stop/failure: $state", state.getString("state") in listOf("starting", "running"))
                        val item = JSONObject().put("index", index).put("hostElapsedMs", SystemClock.elapsedRealtime())
                            .put("status", encode(state))
                        timeline.put(item); persist()
                        val image = File(context.filesDir, "screenshots/${evidence.name}/frame-$index.png")
                        val captureError = worker.screenshot(image.absolutePath)
                        item.put("capturePath", image.absolutePath).put("captureError", captureError)
                        if (captureError.isEmpty()) item.put("captureSha256", acceptanceSha256(image))
                        // A missing endpoint during startup is retained, not called a crash.
                        val qmp = worker.debugQmpSnapshot()
                        item.put("qmp", encode(qmp))
                        if (qmp.getString("error").isNullOrEmpty()) {
                            val raw = File(requireNotNull(qmp.getString("path")))
                            require(raw.length() <= 1024 * 1024) { "Oversized fixed QMP snapshot" }
                            File(evidence, "qmp-$index.jsonl").writeBytes(raw.readBytes())
                        }
                        if (!wokeOnce && state.getString("state") == "running" &&
                            args.getString("allowOneSleepWake") == "true" &&
                            state.getBundle("observations")?.getLong("displaySleeping") == 1L &&
                            SystemClock.elapsedRealtime() + 2000 < deadline) {
                            // One explicit balanced Home pulse, never gated on pre-wake agent liveness.
                            item.put("wakeDownElapsedMs", SystemClock.elapsedRealtime())
                            try { worker.button(0, true); Thread.sleep(150) }
                            finally { worker.button(0, false); item.put("wakeUpElapsedMs", SystemClock.elapsedRealtime()) }
                            wokeOnce = true
                        }
                        persist()
                        index++
                        val remaining = deadline - SystemClock.elapsedRealtime()
                        if (remaining > 0) Thread.sleep(minOf(10_000, remaining))
                    }
                    receipt.put("stage", "diagnosis-complete").put("diagnosisCaptured", true)
                        .put("sampleCount", index).put("oneSleepWakeSent", wokeOnce)
                        .put("terminalWorker", encode(worker.status())).put("completedElapsedMs", SystemClock.elapsedRealtime())
                        .put("scenarioCloseMayPauseGuest", true).put("shutdownConfirmationInferred", false)
                    persist()
                    // The supervisor owns Stop; this test cannot infer or extend its deadline.
                }
            }
        } catch (failure: Throwable) { receipt.put("failure", failure.toString()); throw failure }
        finally { persist() }
    }
}

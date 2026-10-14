package org.lighttouch.android

import android.content.Context
import android.content.Intent
import android.content.ComponentName
import android.content.ServiceConnection
import android.content.pm.ApplicationInfo
import android.os.Bundle
import android.os.Process
import android.os.IBinder
import android.os.SystemClock
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Arms an existing unused worker only. Never starts, resumes or replaces a guest. */
@RunWith(AndroidJUnit4::class)
class GuestSessionSetupTest {
    private fun withExistingWorker(context: Context, action: (IEngine) -> Unit) {
        val connected = CountDownLatch(1)
        var worker: IEngine? = null
        val connection = object : ServiceConnection {
            override fun onServiceConnected(name: ComponentName, binder: IBinder) {
                worker = IEngine.Stub.asInterface(binder); connected.countDown()
            }
            override fun onServiceDisconnected(name: ComponentName) = Unit
        }
        // Flags0 deliberately omit BIND_AUTO_CREATE: no new worker if it died.
        check(context.bindService(Intent(context, EngineService::class.java), connection, 0)) { "Existing worker could not be bound" }
        try {
            check(connected.await(10, TimeUnit.SECONDS)) { "No existing guest worker connection" }
            action(requireNotNull(worker))
        } finally { context.unbindService(connection) }
    }
    @Test fun armExistingUnusedWorkerBeforeOperatorStart() {
        val arguments = InstrumentationRegistry.getArguments()
        assumeTrue("Explicit session setup opt-in required", arguments.getString("armGuestSession") == "true")
        require(arguments.getString("armTouchWireTrace") == "true") { "Explicit touch wire arming required" }
        val expectedApk = requireNotNull(arguments.getString("expectedMainApkSha256")) { "Expected main APK hash required" }
        require(expectedApk.matches(Regex("[0-9a-f]{64}"))) { "Invalid expected APK hash" }
        val expectedPid = arguments.getString("expectedWorkerPid")?.toIntOrNull()
        require(expectedPid != null && expectedPid > 0) { "Existing worker PID required" }
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val evidence = File(context.filesDir, "session-setup-evidence-${System.currentTimeMillis()}").apply { check(mkdir()) }
        val receipt = JSONObject().put("pass", false).put("guestAcceptance", false)
            .put("guestStartedByTest", false).put("workerRestartedByTest", false)
            .put("traceDeliveryVerified", false).put("qmpEndpointVerified", false)
        @Suppress("DEPRECATION")
        fun encode(bundle: Bundle): JSONObject = JSONObject().apply {
            for (key in bundle.keySet()) when (val value = bundle.get(key)) {
                is Bundle -> put(key, encode(value))
                is LongArray -> put(key, JSONArray(value.toList()))
                is IntArray -> put(key, JSONArray(value.toList()))
                else -> put(key, value)
            }
        }
        try {
            assertTrue("Debug build required", context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0)
            val actualApk = acceptanceSha256(File(context.applicationInfo.sourceDir))
            receipt.put("mainApkSha256", actualApk).put("expectedMainApkSha256", expectedApk)
            assertEquals("Installed main APK identity", expectedApk, actualApk)
            withExistingWorker(context) { worker ->
                val initial = worker.status()
                receipt.put("before", encode(initial)).put("beforeHostElapsedNs", SystemClock.elapsedRealtimeNanos())
                assertEquals(Process.myUid(), initial.getInt("uid"))
                assertNotEquals(Process.myPid(), initial.getInt("pid"))
                assertEquals("No replacement worker may be armed", expectedPid, initial.getInt("pid"))
                assertEquals("Existing unused worker required", "stopped", initial.getString("state"))
                assertFalse("Consumed workers cannot be armed", initial.getBoolean("sessionStarted"))
                assertFalse("No native session may exist", initial.getBoolean("nativePollSnapshotValid"))
                assertEquals("Retain actual faults; do not acknowledge them here", "", initial.getString("error"))
                assertEquals(0L, initial.getBundle("observations")!!.getLong("producedFrames"))
                val traceError = worker.debugEnableTouchTrace()
                receipt.put("touchTraceArmError", traceError).put("touchTraceArmAdmissionAccepted", traceError.isEmpty())
                assertEquals("Fixed pre-start touch trace admission", "", traceError)
                val armQmp = arguments.getString("armFixedQmp") == "true"
                receipt.put("fixedQmpArmRequested", armQmp)
                if (armQmp) {
                    val qmpError = worker.debugArmQmp()
                    receipt.put("fixedQmpArmError", qmpError).put("fixedQmpArmAdmissionAccepted", qmpError.isEmpty())
                    assertEquals("Fixed pre-start QMP admission", "", qmpError)
                }
                val after = worker.status()
                receipt.put("after", encode(after)).put("afterHostElapsedNs", SystemClock.elapsedRealtimeNanos())
                    .put("touchTraceConfiguredAfterArm", after.getBoolean("touchTraceConfigured"))
                assertEquals(initial.getInt("pid"), after.getInt("pid")); assertEquals(initial.getInt("uid"), after.getInt("uid"))
                assertEquals("stopped", after.getString("state")); assertFalse(after.getBoolean("sessionStarted"))
                assertFalse(after.getBoolean("nativePollSnapshotValid")); assertEquals("", after.getString("error"))
                assertEquals(0L, after.getBundle("observations")!!.getLong("producedFrames"))
                receipt.put("pass", true).put("workerPid", after.getInt("pid")).put("appUid", after.getInt("uid"))
            }
        } catch (failure: Throwable) { receipt.put("failure", failure.toString()); throw failure }
        finally { File(evidence, "result.json").writeText(receipt.toString(2)) }
    }
}

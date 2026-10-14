package org.lighttouch.android

import android.content.Intent
import android.content.pm.ActivityInfo
import android.content.res.Configuration
import android.os.Bundle
import android.os.Process
import android.os.SystemClock
import android.view.InputDevice
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.accessibility.AccessibilityNodeInfo
import androidx.lifecycle.Lifecycle
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

/** Opt-in real guest exercise, with host observations kept separate from visual proof. */
@RunWith(AndroidJUnit4::class)
class GuestOrientationLifecycleTest {
    @Test fun recreateRotateCancelReuseAndBalanceProductionControls() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val arguments = InstrumentationRegistry.getArguments()
        assumeTrue("Root must boot the legitimate guest first", arguments.getString("exerciseOrientationLifecycle") == "true")
        val context = instrumentation.targetContext
        val evidence = File(context.filesDir, "orientation-evidence-${System.currentTimeMillis()}").apply { check(mkdir()) }
        val result = JSONObject().put("hostChecksPassed", false).put("guestAcceptancePassed", false)
            .put("visibleGuestVerified", false).put("audiblePlaybackVerified", false)
        val observations = JSONArray()
        val events = JSONArray()
        @Suppress("DEPRECATION")
        fun encode(bundle: Bundle): JSONObject = JSONObject().apply {
            for (key in bundle.keySet()) when (val value = bundle.get(key)) {
                is Bundle -> put(key, encode(value))
                is IntArray -> put(key, JSONArray(value.toList()))
                is LongArray -> put(key, JSONArray(value.toList()))
                else -> put(key, value)
            }
        }
        fun persist() {
            File(evidence, "observations.json").writeText(observations.toString(2))
            File(evidence, "events.json").writeText(events.toString(2))
            File(evidence, "result.json").writeText(result.toString(2))
        }
        fun findDisplay(view: View): GuestDisplay? {
            if (view is GuestDisplay) return view
            if (view is ViewGroup) for (index in 0 until view.childCount) findDisplay(view.getChildAt(index))?.let { return it }
            return null
        }
        try {
            ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                var originalOrientation = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
                scenario.onActivity { originalOrientation = it.requestedOrientation }
                AcceptanceBinding(context, Intent(context, EngineService::class.java), IEngine.Stub::asInterface).use { binding ->
                    val worker = binding.get()
                    val initial = worker.status()
                    assertTrue("A real guest must already be running", initial.getString("state") in listOf("running", "paused"))
                    val workerPid = initial.getInt("pid")
                    assertEquals(Process.myUid(), initial.getInt("uid")); assertNotEquals(Process.myPid(), workerPid)
                    result.put("workerPid", workerPid).put("appUid", Process.myUid()).put("originalRequestedOrientation", originalOrientation)
                    fun awaitState(expected: String) {
                        val deadline = SystemClock.elapsedRealtime() + 15_000
                        do {
                            val status = worker.status()
                            assertEquals("Worker changed during lifecycle", workerPid, status.getInt("pid"))
                            check(status.getString("state") != "failed") { status.toString() }
                            if (status.getString("state") == expected) return
                            Thread.sleep(100)
                        } while (SystemClock.elapsedRealtime() < deadline)
                        fail("Expected $expected, actual ${worker.status()}")
                    }
                    fun readyDisplay(expectedOrientation: Int? = null): GuestDisplay {
                        val deadline = SystemClock.elapsedRealtime() + 15_000
                        do {
                            var display: GuestDisplay? = null
                            scenario.onActivity { activity ->
                                if (expectedOrientation == null || activity.resources.configuration.orientation == expectedOrientation) {
                                    findDisplay(activity.window.decorView)?.takeIf { it.width > 0 && it.height > 0 && it.holder.surface.isValid }?.let { display = it }
                                }
                            }
                            display?.let { return it }
                            Thread.sleep(100)
                        } while (SystemClock.elapsedRealtime() < deadline)
                        error("Production Surface/orientation was not ready; OS orientation policy may have rejected this request")
                    }
                    fun snapshot(label: String, screenshot: Boolean = true): Bundle {
                        val status = worker.status()
                        assertEquals("Worker identity", workerPid, status.getInt("pid"))
                        assertEquals("Actionable worker error", "", status.getString("error"))
                        val item = JSONObject().put("label", label).put("hostElapsedMs", SystemClock.elapsedRealtime()).put("status", encode(status))
                        scenario.onActivity { activity ->
                            val display = requireNotNull(findDisplay(activity.window.decorView))
                            item.put("activityIdentity", System.identityHashCode(activity)).put("requestedOrientation", activity.requestedOrientation)
                                .put("configurationOrientation", activity.resources.configuration.orientation).put("displayRotation", display.display.rotation)
                                .put("viewWidth", display.width).put("viewHeight", display.height).put("surfaceValid", display.holder.surface.isValid)
                                .put("androidInput", encode(display.inputObservations()))
                        }
                        observations.put(item); persist()
                        if (screenshot) {
                            val path = File(context.filesDir, "screenshots/${evidence.name}/$label.png")
                            assertEquals("Actual frame screenshot", "", worker.screenshot(path.absolutePath))
                            item.put("screenshot", path.absolutePath); persist()
                        }
                        return requireNotNull(status.getBundle("observations"))
                    }
                    var gestureDown = SystemClock.uptimeMillis()
                    fun send(action: Int, id: Int = 7, x: Float = .5f, y: Float = .5f) {
                        if (action == MotionEvent.ACTION_DOWN) gestureDown = SystemClock.uptimeMillis()
                        val status = worker.status()
                        scenario.onActivity { activity ->
                            val display = requireNotNull(findDisplay(activity.window.decorView))
                            val viewport = GuestTestViewport(status, display.width, display.height)
                            val point = viewport.point(x, y)
                            val property = MotionEvent.PointerProperties().apply { this.id = id; toolType = MotionEvent.TOOL_TYPE_FINGER }
                            val coordinate = MotionEvent.PointerCoords().apply { this.x = point.first; this.y = point.second; pressure = 1f; size = .05f }
                            val eventTime = SystemClock.uptimeMillis()
                            val event = MotionEvent.obtain(gestureDown, eventTime, action, 1, arrayOf(property), arrayOf(coordinate), 0, 0, 1f, 1f, 0, 0, InputDevice.SOURCE_TOUCHSCREEN, 0)
                            try { assertTrue(display.dispatchTouchEvent(event)) } finally { event.recycle() }
                            events.put(JSONObject().put("action", action).put("id", id).put("guestX", x).put("guestY", y)
                                .put("viewXPx", point.first).put("viewYPx", point.second).put("viewport", viewport.receipt())
                                .put("uptimeMs", eventTime).put("displayRotation", display.display.rotation).put("input", encode(display.inputObservations())))
                        }
                        persist(); Thread.sleep(30)
                    }
                    fun resume() { worker.pause(false); awaitState("running"); readyDisplay(); Thread.sleep(300) }
                    fun reuseAndRelease(label: String) {
                        var before = 0L
                        scenario.onActivity { before = requireNotNull(findDisplay(it.window.decorView)).inputObservations().getLong("forwardedBatches") }
                        send(MotionEvent.ACTION_MOVE, 7)
                        scenario.onActivity {
                            val display = requireNotNull(findDisplay(it.window.decorView))
                            assertEquals("Old MOVE forwarded without fresh DOWN", before, display.inputObservations().getLong("forwardedBatches"))
                        }
                        send(MotionEvent.ACTION_DOWN, 7, .48f); send(MotionEvent.ACTION_MOVE, 7, .52f); send(MotionEvent.ACTION_UP, 7, .52f)
                        send(MotionEvent.ACTION_DOWN, 23); send(MotionEvent.ACTION_CANCEL, 23)
                        send(MotionEvent.ACTION_MOVE, 23)
                        send(MotionEvent.ACTION_DOWN, 23); send(MotionEvent.ACTION_UP, 23)
                        scenario.onActivity { assertArrayEquals("Android contacts retained", intArrayOf(), requireNotNull(findDisplay(it.window.decorView)).inputObservations().getIntArray("androidContactIds")) }
                        snapshot(label)
                    }
                    try {
                        resume(); scenario.onActivity { it.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
                        readyDisplay(Configuration.ORIENTATION_PORTRAIT); resume()
                        val baseline = snapshot("portrait-baseline")
                        send(MotionEvent.ACTION_DOWN)
                        val oldDisplay = readyDisplay()
                        var beforeCancel = 0L
                        instrumentation.runOnMainSync { beforeCancel = oldDisplay.inputObservations().getLong("cancellationRequests") }
                        scenario.recreate(); readyDisplay(Configuration.ORIENTATION_PORTRAIT)
                        awaitState("paused")
                        instrumentation.runOnMainSync {
                            assertTrue("Recreation did not cancel old view contacts", oldDisplay.inputObservations().getLong("cancellationRequests") > beforeCancel)
                            assertArrayEquals(intArrayOf(), oldDisplay.inputObservations().getIntArray("androidContactIds"))
                        }
                        resume(); reuseAndRelease("recreated-portrait")
                        for ((orientation, configuration, label) in listOf(
                            Triple(ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE, Configuration.ORIENTATION_LANDSCAPE, "landscape"),
                            Triple(ActivityInfo.SCREEN_ORIENTATION_PORTRAIT, Configuration.ORIENTATION_PORTRAIT, "portrait-return"))) {
                            send(MotionEvent.ACTION_DOWN)
                            val prior = readyDisplay(); var cancels = 0L
                            instrumentation.runOnMainSync { cancels = prior.inputObservations().getLong("cancellationRequests") }
                            scenario.onActivity { it.requestedOrientation = orientation }
                            readyDisplay(configuration); awaitState("paused")
                            instrumentation.runOnMainSync { assertTrue("Orientation failed to cancel held contacts", prior.inputObservations().getLong("cancellationRequests") > cancels) }
                            resume(); reuseAndRelease(label)
                        }
                        send(MotionEvent.ACTION_DOWN)
                        scenario.moveToState(Lifecycle.State.CREATED); awaitState("paused")
                        snapshot("background-surface-loss", false)
                        scenario.moveToState(Lifecycle.State.RESUMED); readyDisplay(); awaitState("paused")
                        resume(); reuseAndRelease("foreground-surface-return")
                        val beforeButtons = snapshot("before-buttons")
                        fun clickControl(text: String) {
                            val deadline = SystemClock.elapsedRealtime() + 5_000
                            do {
                                val node = instrumentation.uiAutomation.rootInActiveWindow?.findAccessibilityNodeInfosByText(text)
                                    ?.firstOrNull { it.packageName?.toString() == context.packageName && it.text?.toString() == text && it.isClickable && it.isEnabled }
                                if (node != null && node.performAction(AccessibilityNodeInfo.ACTION_CLICK)) return
                                Thread.sleep(100)
                            } while (SystemClock.elapsedRealtime() < deadline)
                            error("Semantic production control unavailable: $text")
                        }
                        clickControl("Home"); Thread.sleep(300)
                        clickControl("Power"); Thread.sleep(500); snapshot("power-sleep")
                        clickControl("Power"); Thread.sleep(500); snapshot("power-wake")
                        val afterButtons = snapshot("balanced-buttons")
                        val downBefore = requireNotNull(beforeButtons.getLongArray("buttonDownAdmissions")); val upBefore = requireNotNull(beforeButtons.getLongArray("buttonUpAdmissions"))
                        val downAfter = requireNotNull(afterButtons.getLongArray("buttonDownAdmissions")); val upAfter = requireNotNull(afterButtons.getLongArray("buttonUpAdmissions"))
                        for (button in 0..1) {
                            assertTrue("Button $button never admitted", downAfter[button] > downBefore[button])
                            assertEquals("Unbalanced button $button", downAfter[button] - downBefore[button], upAfter[button] - upBefore[button])
                        }
                        assertEquals(beforeButtons.getLong("buttonRejections"), afterButtons.getLong("buttonRejections"))
                        assertTrue("Production Surface did not reconnect", afterButtons.getLong("surfaceGeneration") > baseline.getLong("surfaceGeneration"))
                        assertTrue("No real frame presented", afterButtons.getLong("presentedFrames") > 0)
                        assertEquals("Window errors increased", baseline.getLong("windowFailures"), afterButtons.getLong("windowFailures"))
                        assertEquals("Frame copy errors increased", baseline.getLong("frameCopyFailures"), afterButtons.getLong("frameCopyFailures"))
                        result.put("hostChecksPassed", true).put("mappingContract", "upright guest portrait pixels in aspect-fit Surface; native maps view-local input without a second rotation")
                    } finally {
                        // Best effort releases never turn a failed test into a pass.
                        try {
                            worker.cancelInput(); worker.button(0, false); worker.button(1, false)
                            result.put("cleanupWorkerStatus", encode(worker.status()))
                        }
                        catch (failure: Exception) { result.put("releaseFailure", failure.toString()) }
                        try { scenario.onActivity { it.requestedOrientation = originalOrientation }; result.put("orientationRestoreRequested", true) }
                        catch (failure: Exception) { result.put("orientationRestoreFailure", failure.toString()) }
                    }
                }
            }
        } catch (failure: Throwable) { result.put("failure", failure.toString()); throw failure }
        finally { persist() }
    }
}

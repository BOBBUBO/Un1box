package org.lighttouch.android

import android.content.Intent
import android.media.*
import android.os.*
import android.view.InputDevice
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
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

/** Observe real production playback/presentation around lifecycle transitions. */
@RunWith(AndroidJUnit4::class)
class GuestMediaLifecycleTest {
    @Test fun productionMediaContinuesAfterPauseSurfaceAndAudioFocusChanges() {
        val arguments = InstrumentationRegistry.getArguments()
        assumeTrue("Requires an actual running guest and explicit media opt-in", arguments.getString("exerciseMedia") == "true")
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val heldFocus = arguments.getString("exerciseHeldAudioFocus") == "true"
        val requireConcurrentWindows = arguments.getString("requireConcurrentMediaWindows") == "true"
        val extended = heldFocus || requireConcurrentWindows
        if (heldFocus) assertEquals("Held focus opt-in also requires real focus exercise", "true", arguments.getString("exerciseAudioFocus"))
        val evidence = File(context.filesDir, "media-evidence-${System.currentTimeMillis()}").apply { check(mkdir()) }
        val result = JSONObject().put("pass", false).put("audiblePlaybackVerified", false).put("visibleGuestVerified", false)
            .put("guestAcceptancePassed", false).put("heldAudioFocusRequested", heldFocus)
            .put("concurrentWindowsRequired", requireConcurrentWindows)
        val samples = JSONArray()
        val intervals = JSONArray(); val events = JSONArray()
        @Suppress("DEPRECATION")
        fun encode(bundle: Bundle): JSONObject = JSONObject().apply {
            for (key in bundle.keySet()) when (val value = bundle.get(key)) {
                is Bundle -> put(key, encode(value))
                is LongArray -> put(key, JSONArray(value.toList()))
                is IntArray -> put(key, JSONArray(value.toList()))
                else -> put(key, value)
            }
        }
        fun findDisplay(view: View): GuestDisplay? {
            if (view is GuestDisplay) return view
            if (view is ViewGroup) for (i in 0 until view.childCount) findDisplay(view.getChildAt(i))?.let { return it }
            return null
        }
        val manager = context.getSystemService(AudioManager::class.java)
        var competingFocus: AudioFocusRequest? = null
        try {
            ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                AcceptanceBinding(context, Intent(context, EngineService::class.java), IEngine.Stub::asInterface).use { binding ->
                    val worker = binding.get()
                    val initial = worker.status()
                    val pid = initial.getInt("pid"); val uid = initial.getInt("uid")
                    var viewAvailable = true
                    fun snapshot(label: String): Bundle {
                        val begin = SystemClock.elapsedRealtimeNanos()
                        val status = worker.status()
                        val item = JSONObject().put("label", label).put("hostElapsedMs", SystemClock.elapsedRealtime()).put("status", encode(status))
                        if (extended) {
                            item.put("readBeginElapsedNs", begin).put("readEndElapsedNs", SystemClock.elapsedRealtimeNanos())
                            if (viewAvailable) scenario.onActivity { activity ->
                                val display = requireNotNull(findDisplay(activity.window.decorView))
                                item.put("androidInput", encode(display.inputObservations())).put("surfaceValid", display.holder.surface.isValid)
                                    .put("viewFocused", display.hasWindowFocus())
                            }
                            else item.put("androidInputUnavailableWhileBackgrounded", true)
                        }
                        samples.put(item)
                        // The extended 1200s trace appends bounded records instead
                        // of rewriting the growing multi-megabyte array every second.
                        if (extended) File(evidence, "observations.jsonl").appendText(item.toString() + "\n")
                        else File(evidence, "observations.json").writeText(samples.toString(2))
                        if (extended) {
                            assertEquals("Worker PID changed", pid, status.getInt("pid")); assertEquals("Worker UID changed", uid, status.getInt("uid"))
                            assertEquals("Current worker failure (historical errors are not cleared)", "", status.getString("error"))
                            if (label == "running") assertEquals("Sustained workload was interrupted", "running", status.getString("state"))
                        }
                        return requireNotNull(status.getBundle("observations"))
                    }
                    fun capture(label: String) {
                        val before = worker.status()
                        val path = File(context.filesDir, "screenshots/${evidence.name}/$label.png")
                        val error = worker.screenshot(path.absolutePath)
                        events.put(JSONObject().put("capture", path.absolutePath).put("label", label).put("error", error)
                            .put("before", encode(before)).put("after", encode(worker.status())).put("atomicFrameBindingClaimed", false))
                        File(evidence, "events.json").writeText(events.toString(2))
                        assertEquals("Actual guest frame capture", "", error)
                    }
                    var downTime = SystemClock.uptimeMillis()
                    fun touch(action: Int, ids: IntArray) {
                        val status = worker.status()
                        scenario.onActivity { activity ->
                            val display = requireNotNull(findDisplay(activity.window.decorView))
                            assertTrue("Production Surface is unavailable", display.holder.surface.isValid)
                            val viewport = GuestTestViewport(status, display.width, display.height)
                            if ((action and MotionEvent.ACTION_MASK) == MotionEvent.ACTION_DOWN) downTime = SystemClock.uptimeMillis()
                            val points = ids.map { viewport.point(if (it == 7) .45f else .55f, .5f) }
                            val properties = ids.map { MotionEvent.PointerProperties().apply { id = it; toolType = MotionEvent.TOOL_TYPE_FINGER } }.toTypedArray()
                            val coords = points.map { MotionEvent.PointerCoords().apply { x = it.first; y = it.second; pressure = 1f; size = .05f } }.toTypedArray()
                            val at = SystemClock.uptimeMillis()
                            val event = MotionEvent.obtain(downTime, at, action, ids.size, properties, coords, 0, 0, 1f, 1f, 0, 0, InputDevice.SOURCE_TOUCHSCREEN, 0)
                            try { assertTrue(display.dispatchTouchEvent(event)) } finally { event.recycle() }
                            events.put(JSONObject().put("action", action).put("ids", JSONArray(ids.toList())).put("rawUptimeMs", at)
                                .put("viewport", viewport.receipt()).put("androidInput", encode(display.inputObservations())))
                        }
                        File(evidence, "events.json").writeText(events.toString(2))
                    }
                    fun input(): Bundle {
                        var value: Bundle? = null
                        scenario.onActivity { value = requireNotNull(findDisplay(it.window.decorView)).inputObservations() }
                        return requireNotNull(value)
                    }
                    fun awaitState(expected: String) {
                        val deadline = SystemClock.elapsedRealtime() + 10_000
                        while (worker.status().getString("state") != expected && SystemClock.elapsedRealtime() < deadline) Thread.sleep(100)
                        assertEquals("Lifecycle state", expected, worker.status().getString("state"))
                    }
                    var exerciseFailure: Throwable? = null
                    try {
                    assertTrue("Root must boot the selected real guest first", initial.getString("state") in listOf("running", "paused"))
                    if (extended) assertEquals("Resolve the existing worker failure before measuring; no acknowledgement shortcut", "", initial.getString("error"))
                    if (initial.getString("state") == "paused") worker.pause(false)
                    awaitState("running")
                    val before = snapshot("measurement-start")
                    if (extended) capture("measurement-start")
                    val durationMs = arguments.getString("mediaSeconds", "30").toLong().coerceIn(5, 1800) * 1000
                    val deadline = SystemClock.elapsedRealtime() + durationMs
                    var previous = before; var blockStart = SystemClock.elapsedRealtime(); var qualifyingInBlock = 0; var totalQualifying = 0
                    while (SystemClock.elapsedRealtime() < deadline) {
                        Thread.sleep(minOf(1000, (deadline - SystemClock.elapsedRealtime()).coerceAtLeast(1)))
                        val current = snapshot("running")
                        if (extended) {
                            val interval = JSONObject().put("fromSample", samples.length() - 2).put("toSample", samples.length() - 1)
                                .put("guestConsumptionInferred", false).put("continuousTwoContactsClaimed", false)
                            fun delta(key: String, old: Bundle, now: Bundle): Long = (now.getLong(key) - old.getLong(key)).also {
                                assertTrue("Counter reset: $key", it >= 0); interval.put(key + "Delta", it)
                            }
                            val old = requireNotNull(previous.getBundle("audio")); val audio = requireNotNull(current.getBundle("audio"))
                            val overlap = delta("inputRequests", previous, current) > 0 &&
                                delta("producedFrames", previous, current) > 0 && delta("presentedFrames", previous, current) > 0 &&
                                delta("pulledNonzeroSamples", old, audio) > 0 && delta("writtenNonzeroSamples", old, audio) > 0 &&
                                delta("playedFrames", old, audio) > 0
                            // Retain every delta even when an earlier condition is zero.
                            for (key in listOf("inputRequests", "producedFrames", "presentedFrames")) if (!interval.has(key + "Delta")) delta(key, previous, current)
                            for (key in listOf("pulledNonzeroSamples", "writtenNonzeroSamples", "playedFrames", "underruns")) if (!interval.has(key + "Delta")) delta(key, old, audio)
                            interval.put("inputFrameAndNonzeroPlaybackCounterOverlap", overlap)
                            intervals.put(interval); File(evidence, "intervals.jsonl").appendText(interval.toString() + "\n")
                            if (overlap) { qualifyingInBlock++; totalQualifying++ }
                            for (key in listOf("writeFailures", "nativePullFailures")) assertEquals("Audio failure increased: $key", old.getLong(key), audio.getLong(key))
                            for (key in listOf("windowFailures", "frameCopyFailures")) assertEquals("Presentation failure increased: $key", previous.getLong(key), current.getLong(key))
                            if (SystemClock.elapsedRealtime() - blockStart >= 30_000) {
                                if (requireConcurrentWindows) assertTrue("No input/frame/nonzero playback interval in this declared workload block (at most30s)", qualifyingInBlock > 0)
                                blockStart = SystemClock.elapsedRealtime(); qualifyingInBlock = 0
                            }
                        }
                        previous = current
                    }
                    if (requireConcurrentWindows && SystemClock.elapsedRealtime() - blockStart >= 5_000)
                        assertTrue("No concurrent counter interval in the final workload block", qualifyingInBlock > 0)
                    result.put("qualifyingCounterOverlapIntervals", totalQualifying)
                    val measured = snapshot("measurement-end")
                    if (extended) capture("measurement-end")
                    assertTrue("No actual guest frame reached Surface", measured.getLong("presentedFrames") > 0)
                    val oldAudio = requireNotNull(before.getBundle("audio")); val newAudio = requireNotNull(measured.getBundle("audio"))
                    assertEquals("Audio write failures increased", oldAudio.getLong("writeFailures"), newAudio.getLong("writeFailures"))
                    assertEquals("Native PCM failures increased", oldAudio.getLong("nativePullFailures"), newAudio.getLong("nativePullFailures"))
                    if (arguments.getString("requireFrameProgress") == "true") assertTrue("Chosen animation did not present new frames", measured.getLong("presentedFrames") > before.getLong("presentedFrames"))
                    if (arguments.getString("requireGuestAudio") == "true") {
                        assertTrue("Guest workload supplied no nonzero PCM", newAudio.getLong("pulledNonzeroSamples") > oldAudio.getLong("pulledNonzeroSamples"))
                        assertTrue("AudioTrack accepted no nonzero guest samples", newAudio.getLong("writtenNonzeroSamples") > oldAudio.getLong("writtenNonzeroSamples"))
                        assertTrue("AudioTrack playback head did not advance", newAudio.getLong("playedFrames") > oldAudio.getLong("playedFrames"))
                    }
                    worker.pause(true); awaitState("paused"); Thread.sleep(300)
                    val paused = snapshot("user-paused")
                    Thread.sleep(700)
                    assertEquals("Audio written while user paused", paused.getBundle("audio")!!.getLong("writtenSamples"), snapshot("pause-stable").getBundle("audio")!!.getLong("writtenSamples"))
                    worker.pause(false); awaitState("running"); snapshot("user-resumed")
                    viewAvailable = false
                    scenario.moveToState(Lifecycle.State.CREATED); awaitState("paused"); snapshot("background-surface-loss")
                    scenario.moveToState(Lifecycle.State.RESUMED)
                    viewAvailable = true
                    assertEquals("Reopening must preserve explicit pause", "paused", worker.status().getString("state"))
                    worker.pause(false); awaitState("running"); snapshot("surface-return-resumed")
                    if (arguments.getString("exerciseAudioFocus") == "true") {
                        var heldStatus: Bundle? = null; var heldInput: Bundle? = null
                        if (heldFocus) {
                            touch(MotionEvent.ACTION_DOWN, intArrayOf(7))
                            touch(MotionEvent.ACTION_POINTER_DOWN or (1 shl MotionEvent.ACTION_POINTER_INDEX_SHIFT), intArrayOf(7, 23))
                            heldStatus = worker.status(); heldInput = input()
                            assertArrayEquals(intArrayOf(7, 23), heldInput.getIntArray("androidContactIds"))
                            assertTrue("Actual generation is unavailable", heldStatus.getLong("displayInputGeneration") > 0)
                            assertEquals(heldStatus.getLong("displayInputGeneration"), heldInput.getLong("inputGeneration"))
                            capture("focus-two-held")
                        }
                        val focus = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
                            .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build())
                            .setOnAudioFocusChangeListener({ }, Handler(Looper.getMainLooper())).build()
                        competingFocus = focus
                        assertEquals(AudioManager.AUDIOFOCUS_REQUEST_GRANTED, manager.requestAudioFocus(focus))
                        awaitState("paused"); assertTrue(snapshot("focus-lost").getBoolean("focusPaused"))
                        manager.abandonAudioFocusRequest(focus); competingFocus = null
                        awaitState("running"); assertFalse(snapshot("focus-restored").getBoolean("focusPaused"))
                        if (heldFocus) {
                            val restored = worker.status(); val old = requireNotNull(heldStatus); val priorInput = requireNotNull(heldInput)
                            assertEquals("Display owner changed; this did not isolate audio-focus cancellation", old.getLong("displayInputOwner"), restored.getLong("displayInputOwner"))
                            assertTrue("Full cancellation did not advance the native generation", restored.getLong("displayInputGeneration") > old.getLong("displayInputGeneration"))
                            assertArrayEquals("View callbacks already cancelled contacts; this did not exercise unnotified focus cancellation", intArrayOf(7, 23), input().getIntArray("androidContactIds"))
                            capture("focus-restored-before-continuation")
                            val requests = restored.getBundle("observations")!!.getLong("inputRequests")
                            touch(MotionEvent.ACTION_MOVE, intArrayOf(7, 23))
                            val cancelled = input()
                            assertArrayEquals(intArrayOf(), cancelled.getIntArray("androidContactIds"))
                            assertTrue(cancelled.getBoolean("awaitingNewDown"))
                            assertEquals(priorInput.getLong("expectedCancellationBatches") + 1, cancelled.getLong("expectedCancellationBatches"))
                            assertEquals(priorInput.getLong("rejectedBatches"), cancelled.getLong("rejectedBatches"))
                            touch(MotionEvent.ACTION_POINTER_UP or (1 shl MotionEvent.ACTION_POINTER_INDEX_SHIFT), intArrayOf(7, 23))
                            touch(MotionEvent.ACTION_UP, intArrayOf(7))
                            assertEquals("Old continuations reached native input admission", requests, worker.status().getBundle("observations")!!.getLong("inputRequests"))
                            touch(MotionEvent.ACTION_DOWN, intArrayOf(7)); touch(MotionEvent.ACTION_UP, intArrayOf(7))
                            assertArrayEquals(intArrayOf(), input().getIntArray("androidContactIds"))
                            assertEquals("Fresh DOWN/UP was not admitted", requests + 2, worker.status().getBundle("observations")!!.getLong("inputRequests"))
                            Thread.sleep(500); snapshot("focus-fresh-down-reused"); capture("focus-fresh-down-reused")
                            result.put("heldFocusHostChecksPassed", true)
                        }
                    }
                    snapshot("terminal")
                    result.put("pass", true).put("workerPid", initial.getInt("pid")).put("appUid", initial.getInt("uid"))
                        .put("measurementMs", durationMs).put("requiresAudio", arguments.getString("requireGuestAudio") == "true")
                        .put("requiresFrameProgress", arguments.getString("requireFrameProgress") == "true")
                    } catch (failure: Throwable) { exerciseFailure = failure; throw failure }
                    finally {
                        if (heldFocus) {
                            var cleanup: Throwable? = null
                            fun release(action: () -> Unit) { try { action() } catch (failure: Throwable) { if (cleanup == null) cleanup = failure else cleanup!!.addSuppressed(failure) } }
                            release { val focus = competingFocus; competingFocus = null; focus?.let { manager.abandonAudioFocusRequest(it) } }
                            if (viewAvailable) release { scenario.onActivity { findDisplay(it.window.decorView)?.cancelOwnedGesture() } }
                            release { worker.cancelInput() }
                            cleanup?.let { failure ->
                                result.put("pass", false).put("cleanupFailure", failure.toString())
                                if (exerciseFailure == null) throw failure else exerciseFailure!!.addSuppressed(failure)
                            }
                        }
                    }
                }
            }
        } catch (failure: Throwable) { result.put("failure", failure.toString()); throw failure }
        finally {
            competingFocus?.let { manager.abandonAudioFocusRequest(it) }
            if (extended) {
                File(evidence, "observations.json").writeText(samples.toString(2))
                File(evidence, "intervals.json").writeText(intervals.toString(2))
                File(evidence, "events.json").writeText(events.toString(2))
            }
            File(evidence, "result.json").writeText(result.toString(2))
        }
    }
}

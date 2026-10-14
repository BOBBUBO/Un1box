package org.lighttouch.android

import android.content.Intent
import android.media.*
import android.os.*
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
        val evidence = File(context.filesDir, "media-evidence-${System.currentTimeMillis()}").apply { check(mkdir()) }
        val result = JSONObject().put("pass", false).put("audiblePlaybackVerified", false).put("visibleGuestVerified", false)
        val samples = JSONArray()
        @Suppress("DEPRECATION")
        fun encode(bundle: Bundle): JSONObject = JSONObject().apply {
            for (key in bundle.keySet()) when (val value = bundle.get(key)) { is Bundle -> put(key, encode(value)); else -> put(key, value) }
        }
        val manager = context.getSystemService(AudioManager::class.java)
        var competingFocus: AudioFocusRequest? = null
        try {
            ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                AcceptanceBinding(context, Intent(context, EngineService::class.java), IEngine.Stub::asInterface).use { binding ->
                    val worker = binding.get()
                    fun snapshot(label: String): Bundle {
                        val status = worker.status()
                        samples.put(JSONObject().put("label", label).put("hostElapsedMs", SystemClock.elapsedRealtime()).put("status", encode(status)))
                        File(evidence, "observations.json").writeText(samples.toString(2))
                        return requireNotNull(status.getBundle("observations"))
                    }
                    fun awaitState(expected: String) {
                        val deadline = SystemClock.elapsedRealtime() + 10_000
                        while (worker.status().getString("state") != expected && SystemClock.elapsedRealtime() < deadline) Thread.sleep(100)
                        assertEquals("Lifecycle state", expected, worker.status().getString("state"))
                    }
                    val initial = worker.status()
                    assertTrue("Root must boot the selected real guest first", initial.getString("state") in listOf("running", "paused"))
                    if (initial.getString("state") == "paused") worker.pause(false)
                    awaitState("running")
                    val before = snapshot("measurement-start")
                    val durationMs = arguments.getString("mediaSeconds", "30").toLong().coerceIn(5, 1800) * 1000
                    val deadline = SystemClock.elapsedRealtime() + durationMs
                    while (SystemClock.elapsedRealtime() < deadline) { Thread.sleep(1000); snapshot("running") }
                    val measured = snapshot("measurement-end")
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
                    scenario.moveToState(Lifecycle.State.CREATED); awaitState("paused"); snapshot("background-surface-loss")
                    scenario.moveToState(Lifecycle.State.RESUMED)
                    assertEquals("Reopening must preserve explicit pause", "paused", worker.status().getString("state"))
                    worker.pause(false); awaitState("running"); snapshot("surface-return-resumed")
                    if (arguments.getString("exerciseAudioFocus") == "true") {
                        val focus = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
                            .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build())
                            .setOnAudioFocusChangeListener({ }, Handler(Looper.getMainLooper())).build()
                        competingFocus = focus
                        assertEquals(AudioManager.AUDIOFOCUS_REQUEST_GRANTED, manager.requestAudioFocus(focus))
                        awaitState("paused"); assertTrue(snapshot("focus-lost").getBoolean("focusPaused"))
                        manager.abandonAudioFocusRequest(focus); competingFocus = null
                        awaitState("running"); assertFalse(snapshot("focus-restored").getBoolean("focusPaused"))
                    }
                    snapshot("terminal")
                    result.put("pass", true).put("workerPid", initial.getInt("pid")).put("appUid", initial.getInt("uid"))
                        .put("measurementMs", durationMs).put("requiresAudio", arguments.getString("requireGuestAudio") == "true")
                        .put("requiresFrameProgress", arguments.getString("requireFrameProgress") == "true")
                }
            }
        } catch (failure: Throwable) { result.put("failure", failure.toString()); throw failure }
        finally { competingFocus?.let { manager.abandonAudioFocusRequest(it) }; File(evidence, "result.json").writeText(result.toString(2)) }
    }
}

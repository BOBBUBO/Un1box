package org.lighttouch.android

import android.content.Intent
import android.os.*
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
import java.security.MessageDigest
import java.util.zip.ZipFile

/** Debug-only, fixed read-only diagnosis; never passes commands or paths. */
@RunWith(AndroidJUnit4::class)
class GuestQmpSnapshotTest {
    @Test fun readRegistersFromDerivedPrivateEndpointInAppUid() {
        val arguments = InstrumentationRegistry.getArguments()
        assumeTrue("Explicit debugGuestQmp opt-in required", arguments.getString("debugGuestQmp") == "true")
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val evidence = File(context.filesDir, "qmp-evidence-${System.currentTimeMillis()}").apply { check(mkdir()) }
        val receipt = JSONObject().put("diagnosisCaptured", false).put("guestBootProven", false)
        val snapshots = JSONArray()
        try {
            receipt.put("installedApkSha256", acceptanceSha256(File(context.applicationInfo.sourceDir)))
            ZipFile(context.applicationInfo.sourceDir).use { apk ->
                val entry = requireNotNull(apk.getEntry("lib/${Build.SUPPORTED_ABIS[0]}/libqemu-arm.so"))
                val digest = MessageDigest.getInstance("SHA-256")
                apk.getInputStream(entry).use { input -> val buffer = ByteArray(65536); while (true) { val size = input.read(buffer); if (size < 0) break; digest.update(buffer, 0, size) } }
                receipt.put("packagedEngineSha256", digest.digest().joinToString("") { "%02x".format(it) })
            }
            receipt.put("firmwareNoticeManifest", JSONObject(context.assets.open("licenses/firmware/manifest.json").bufferedReader().use { it.readText() }))
            ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                AcceptanceBinding(context, Intent(context, EngineService::class.java), IEngine.Stub::asInterface).use { binding ->
                    val worker = binding.get()
                    val initial = worker.status()
                    assertFalse("Root must dispose any consumed worker first", initial.getBoolean("sessionStarted"))
                    assertEquals("stopped", initial.getString("state"))
                    assertEquals(Process.myUid(), initial.getInt("uid")); assertNotEquals(Process.myPid(), initial.getInt("pid"))
                    assertEquals("", worker.debugArmQmp())
                    val record = arguments.getString("recordId") ?: requireNotNull(worker.records().firstOrNull { it.getBoolean("selected") }?.getString("id"))
                    scenario.onActivity { it.startForegroundService(Intent(it, EngineService::class.java)) }
                    val started = worker.start(record)
                    assertEquals(started.toString(), "", started.getString("error"))
                    val endpoint = File(requireNotNull(started.getString("debugQmpPath")))
                    val deadline = SystemClock.elapsedRealtime() + 120_000
                    while (!endpoint.exists() && SystemClock.elapsedRealtime() < deadline) {
                        val state = worker.status(); check(state.getString("state") != "failed") { state.toString() }; Thread.sleep(200)
                    }
                    assertTrue("Private QMP endpoint unavailable; inspect engineLog", endpoint.exists())
                    Thread.sleep(arguments.getString("qmpSettleSeconds", "30").toLong().coerceIn(0, 180) * 1000)
                    repeat(arguments.getString("qmpSamples", "3").toInt().coerceIn(1, 12)) { index ->
                        val before = worker.status()
                        val captured = worker.debugQmpSnapshot()
                        val item = JSONObject().put("index", index).put("error", captured.getString("error"))
                            .put("path", captured.getString("path")).put("engineState", before.getString("state"))
                            .put("engineLog", before.getString("engineLog")).put("hostElapsedMs", SystemClock.elapsedRealtime())
                        snapshots.put(item); File(evidence, "snapshots.json").writeText(snapshots.toString(2))
                        assertEquals(captured.toString(), "", captured.getString("error"))
                        val lines = File(requireNotNull(captured.getString("path"))).readLines().map { JSONObject(it) }
                        assertTrue("Missing QMP greeting", lines.first().has("QMP"))
                        val responses = lines.drop(1).associateBy { it.getString("id") }
                        for (id in listOf("lt-cap", "lt-status", "lt-cpus", "lt-registers")) {
                            val response = requireNotNull(responses[id]); assertFalse(response.toString(), response.has("error"))
                        }
                        item.put("status", responses.getValue("lt-status").getJSONObject("return"))
                            .put("registers", responses.getValue("lt-registers").getString("return"))
                        File(evidence, "snapshots.json").writeText(snapshots.toString(2))
                        Thread.sleep(3000)
                    }
                    receipt.put("recordId", record).put("diagnosisCaptured", true).put("workerPid", initial.getInt("pid"))
                        .put("appUid", Process.myUid()).put("privateEndpoint", endpoint.absolutePath).put("snapshots", snapshots)
                }
            }
        } catch (failure: Throwable) { receipt.put("failure", failure.toString()); throw failure }
        finally { File(evidence, "result.json").writeText(receipt.toString(2)) }
    }
}

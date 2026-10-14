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
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Expensive real preparation is never part of the default test suite. */
@RunWith(AndroidJUnit4::class)
class FirmwareAcceptanceTest {
    @Test fun cancelAfterSystemEditsThenPrepareAndImportInFreshWorker() {
        assumeTrue("Explicit userspace firmware opt-in required", InstrumentationRegistry.getArguments().getString("preparePrivateFirmware") == "true")
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val root = File(context.filesDir, "acceptance-fw")
        val stock = File(root, "stock.ipsw")
        val handoff = File(root, "handoff")
        val expected = JSONObject(File(root, "input-sha256.json").readText())
        val inventory = File(handoff, "inventory.json")
        val tools = File(handoff, "release-guest-unpacked/guest-tools")
        val inputs = listOf(stock, inventory) + tools.walkTopDown().filter { it.isFile }.toList()
        assertTrue(stock.isFile); assertTrue(inventory.isFile); assertTrue(tools.isDirectory)
        assertTrue("Hash receipt must cover every staged input", inputs.size >= 9)
        fun checkInputs() { for (file in inputs) assertEquals("Input hash ${file.relativeTo(root)}", expected.getString(file.relativeTo(root).invariantSeparatorsPath), acceptanceSha256(file)) }
        val evidence = File(context.filesDir, "firmware-evidence-${System.currentTimeMillis()}").apply { check(mkdir()) }
        val result = JSONObject().put("pass", false).put("guestBootProven", false)
        val trace = JSONArray()
        val id = InstrumentationRegistry.getArguments().getString("firmwareId", "n72ap-7E18")
        val timeoutMs = InstrumentationRegistry.getArguments().getString("firmwareTimeoutSeconds", "1800").toLong().coerceIn(60, 7200) * 1000
        try {
            checkInputs()
            ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                var previousPid = 0
                for (cancelAfterEdits in listOf(true, false)) {
                    val intent = Intent(context, FirmwareService::class.java).putExtra("acceptanceCancelAfterSystemEdits", cancelAfterEdits)
                    AcceptanceBinding(context, intent, IFirmware.Stub::asInterface).use { binding ->
                        val worker = binding.get()
                        val initial = worker.status()
                        assertEquals("Fresh preparer required", "idle", initial.getString("state"))
                        assertEquals(Process.myUid(), initial.getInt("uid"))
                        assertNotEquals(Process.myPid(), initial.getInt("pid"))
                        if (previousPid != 0) assertNotEquals("Retry reused consumed process", previousPid, initial.getInt("pid"))
                        previousPid = initial.getInt("pid")
                        assertTrue("Native route discovery", worker.supports(id))
                        scenario.onActivity { it.startForegroundService(Intent(it, FirmwareService::class.java)) }
                        val started = SystemClock.elapsedRealtime()
                        val job = worker.start(id, stock.absolutePath, handoff.absolutePath)
                        val destination = File(requireNotNull(job.getString("destination")))
                        var terminal = job
                        var lastStage = ""
                        while (terminal.getString("state") in listOf("starting", "preparing", "cancelling")) {
                            terminal = worker.status()
                            val stage = terminal.getString("stage") ?: ""
                            if (stage != lastStage) {
                                trace.put(JSONObject().put("pid", previousPid).put("stage", stage).put("done", terminal.getLong("done"))
                                    .put("total", terminal.getLong("total")).put("elapsedMs", SystemClock.elapsedRealtime() - started))
                                File(evidence, "progress.json").writeText(trace.toString(2)); lastStage = stage
                            }
                            if (SystemClock.elapsedRealtime() - started > timeoutMs) { worker.cancel(); error("Preparation timed out; inspect retained native diagnostics") }
                            Thread.sleep(100)
                        }
                        File(evidence, if (cancelAfterEdits) "cancel-result.txt" else "complete-result.txt").writeText(terminal.toString())
                        result.put(if (cancelAfterEdits) "cancelNativeLog" else "prepareNativeLog", destination.absolutePath + ".native.log")
                        result.put(if (cancelAfterEdits) "cancelElapsedMs" else "prepareElapsedMs", SystemClock.elapsedRealtime() - started)
                        if (cancelAfterEdits) {
                            assertTrue("Must reach checkpoint after real system edits", terminal.getBoolean("cancellationCheckpointReached"))
                            assertEquals(terminal.toString(), "cancelled", terminal.getString("state")); assertEquals(6, terminal.getInt("code"))
                            assertFalse("Cancelled output admitted", destination.exists())
                            assertTrue("Cancelled staging leaked", destination.parentFile!!.listFiles()!!.none { it.name.startsWith(".${destination.name}.prepare-") })
                        } else {
                            assertEquals(terminal.toString(), "complete", terminal.getString("state")); assertEquals(0, terminal.getInt("code"))
                            assertTrue(destination.isDirectory)
                            AcceptanceBinding(context, Intent(context, EngineService::class.java), IEngine.Stub::asInterface).use { engineBinding ->
                                val engine = engineBinding.get()
                                assertEquals("Import must not interrupt a guest", "stopped", engine.status().getString("state"))
                                val before = engine.records().map { it.getString("id") }.toSet()
                                val importStart = SystemClock.elapsedRealtime()
                                assertEquals("Canonical prepared base integrity admission", "", engine.importPrepared(destination.absolutePath, "$id Android prepared"))
                                val after = engine.records()
                                assertTrue("Existing baseline lost", after.map { it.getString("id") }.containsAll(before))
                                val record = after.single { it.getString("id") !in before }
                                assertEquals("n72ap", record.getString("board")); assertEquals("7E18", record.getString("build"))
                                result.put("recordId", record.getString("id")).put("importElapsedMs", SystemClock.elapsedRealtime() - importStart)
                                result.put("preparedManifestSha256", acceptanceSha256(File(destination, "device.lock.json")))
                            }
                        }
                        checkInputs()
                        val died = CountDownLatch(1)
                        val binder = worker.asBinder()
                        val recipient = IBinder.DeathRecipient { died.countDown() }
                        binder.linkToDeath(recipient, 0)
                        worker.finish()
                        assertTrue("Consumed firmware process did not exit", died.await(10, TimeUnit.SECONDS))
                        try { binder.unlinkToDeath(recipient, 0) } catch (_: java.util.NoSuchElementException) { }
                    }
                }
                result.put("pass", true).put("appUid", Process.myUid()).put("freshRetryPid", previousPid)
            }
        } catch (failure: Throwable) { result.put("failure", failure.toString()); throw failure }
        finally { File(evidence, "result.json").writeText(result.toString(2)) }
    }
}

package org.un1box.android

import android.content.Intent
import android.os.*
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

@RunWith(AndroidJUnit4::class)
class PrivateFixtureImportTest {
    @Test fun validatedPrivateFixtureAddsIndependentRecordViaProductionBinder() {
        assumeTrue("Explicit private fixture opt-in required", InstrumentationRegistry.getArguments().getString("importPrivateFixture") == "true")
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val inputs = listOf(
            File(context.filesDir, "private-fixtures/n72ap-7E18.itbase") to "7b143e5a63c563bfd039529b863d941204057912bef79f7bd7d58366d388496c",
            File(context.filesDir, "bootrom.bin") to "55f4d8ea2791ba51dd89934168f38f0fb21ce8762ff614c1e742407c0d3ca054",
            File(context.filesDir, "guest.itpack") to "fe25b1406b1153837075c56944fadbac7406840667215f232d16fafca079f45b")
        val evidence = File(context.filesDir, "import-evidence-${System.currentTimeMillis()}").apply { check(mkdir()) }
        val result = JSONObject().put("pass", false).put("guestBootProven", false)
        val executor = Executors.newSingleThreadExecutor()
        try {
            for ((file, hash) in inputs) assertEquals("Provisioned input ${file.name}", hash, acceptanceSha256(file))
            AcceptanceBinding(context, Intent(context, EngineService::class.java), IEngine.Stub::asInterface).use { binding ->
                val worker = binding.get()
                val initial = worker.status()
                assertEquals("Import must not interrupt a guest", "stopped", initial.getString("state"))
                assertNotEquals(Process.myPid(), initial.getInt("pid"))
                assertEquals(Process.myUid(), initial.getInt("uid"))
                val before = worker.records().associateBy { requireNotNull(it.getString("id")) }
                val started = SystemClock.elapsedRealtime()
                val operation = executor.submit<String> { worker.importBase(inputs.first().first.absolutePath, "Verified n72 fixture") }
                var maximumStatusMs = 0L
                while (!operation.isDone) {
                    val queried = SystemClock.elapsedRealtime()
                    worker.status()
                    maximumStatusMs = maxOf(maximumStatusMs, SystemClock.elapsedRealtime() - queried)
                    Thread.sleep(200)
                    check(SystemClock.elapsedRealtime() - started < 30 * 60_000) { "Fixture import timed out" }
                }
                assertEquals("Native integrity admission", "", operation.get(1, TimeUnit.SECONDS))
                val after = worker.records().associateBy { requireNotNull(it.getString("id")) }
                assertEquals(before.size + 1, after.size)
                for ((id, old) in before) {
                    val retained = requireNotNull(after[id]) { "Baseline record was lost" }
                    for (field in listOf("name", "board", "build")) assertEquals("Preserved $id/$field", old.getString(field), retained.getString(field))
                    assertEquals("Preserved $id/dirty", old.getBoolean("dirty"), retained.getBoolean("dirty"))
                }
                val added = after.filterKeys { it !in before }.values.single()
                assertEquals("n72ap", added.getString("board")); assertEquals("7E18", added.getString("build"))
                assertTrue(added.getString("contacts")!!.toInt() >= 2)
                assertTrue(added.getBoolean("bootromAvailable")); assertTrue(added.getBoolean("guestPackageAvailable"))
                assertEquals("", worker.select(added.getString("id")))
                assertEquals(added.getString("id"), worker.records().single { it.getBoolean("selected") }.getString("id"))
                assertTrue("Status blocked during integrity work: $maximumStatusMs ms", maximumStatusMs < 2000)
                for ((file, hash) in inputs) assertEquals("Input unchanged ${file.name}", hash, acceptanceSha256(file))
                result.put("pass", true).put("recordId", added.getString("id")).put("beforeIds", JSONArray(before.keys))
                    .put("workerPid", initial.getInt("pid")).put("appUid", Process.myUid())
                    .put("elapsedMs", SystemClock.elapsedRealtime() - started).put("maximumStatusMs", maximumStatusMs)
            }
        } catch (failure: Throwable) { result.put("failure", failure.toString()); throw failure }
        finally { executor.shutdown(); File(evidence, "result.json").writeText(result.toString(2)) }
    }
}

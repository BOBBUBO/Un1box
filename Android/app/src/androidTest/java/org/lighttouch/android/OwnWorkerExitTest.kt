package org.lighttouch.android

import android.os.Build
import android.app.ApplicationExitInfo
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

@RunWith(AndroidJUnit4::class)
class OwnWorkerExitTest {
    @Test fun retainBoundedNativeTraceForExplicitOwnEnginePid() {
        val pidArgument = InstrumentationRegistry.getArguments().getString("workerPid")
        assumeTrue("Explicit workerPid and API30+ required", pidArgument != null && Build.VERSION.SDK_INT >= 30)
        val pid = requireNotNull(pidArgument).toInt()
        require(pid > 0)
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        WorkerExitEvidence.capture(context, pid)
        val directory = File(context.filesDir, "worker-exits")
        val receipt = requireNotNull(directory.listFiles()?.filter { it.name.matches(Regex("engine-$pid-[0-9]+\\.json")) }?.maxByOrNull { it.name }) {
            "No own :engine exit record for PID$pid. Inspect private worker-exits/*unavailable.json or *capture-error.json."
        }
        val metadata = JSONObject(receipt.readText())
        assertEquals(pid, metadata.getInt("pid"))
        assertEquals("${context.packageName}:engine", metadata.getString("process"))
        assertEquals("Requested known native crash", ApplicationExitInfo.REASON_CRASH_NATIVE, metadata.getInt("reason"))
        assertFalse("Own trace unavailable: $metadata", metadata.optBoolean("traceUnavailable"))
        assertFalse("Own trace read failed: $metadata", metadata.has("traceError"))
        val trace = File(metadata.getString("tracePath"))
        assertTrue("Native trace is empty", trace.length() > 0)
        assertTrue("Trace exceeded fixed 4MiB cap", trace.length() <= 4 * 1024 * 1024)
        // Preserve the raw bytes for tombstone/protobuf decoding by the integration
        // owner. A retrieved trace diagnoses evidence availability, not guest success.
        File(directory, "requested-$pid-result.json").writeText(JSONObject().put("ownNativeTraceRetrieved", true)
            .put("pid", pid).put("receipt", receipt.absolutePath).put("trace", trace.absolutePath)
            .put("guestBootProven", false).toString(2))
    }
}

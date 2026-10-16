package org.lighttouch.android

import android.net.LocalSocket
import android.net.LocalSocketAddress
import android.os.Bundle
import android.os.SystemClock
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.File

/** Instrumentation-only fixed read-only QMP requests; never drives a display update. */
internal class FrameTimelineProbe(
    private val filesDir: File,
    evidence: File,
    private val workerPid: Int,
    private val workerUid: Int
) {
    private val directory = File(evidence, "frame-timeline").apply { check(mkdir()) }
    private var count = 0
    private var failed = false
    private var previousTimeline: FrameTimelineEvidence.Timeline? = null
    private var previousCounters: Map<String, Long>? = null
    private val clock = FrameTimelineEvidence.Clock { SystemClock.elapsedRealtime() }
    private val counterNames = listOf("producedFrames", "producerFrameSequence", "producerFrameTimestampNs",
        "framePublishedHostNs", "presentedFrames", "guestFrameSequence", "guestFrameTimestampNs",
        "lastPresentHostNs", "frameCopyFailures", "windowFailures", "inputRequests")

    fun capture(label: String, status: Bundle): JSONObject {
        require(label.matches(Regex("[a-z0-9-]{1,100}")))
        val result = JSONObject().put("label", label).put("pass", false)
            .put("readOnly", true).put("atomicFrameBindingClaimed", false)
            .put("guestPixelChangeInferred", false).put("guestInputConsumptionInferred", false)
            .put("engineCaptureDirtyAndCopyCountersAvailable", false)
            .put("hostElapsedBeginMs", clock.millis())
        if (failed) return result.put("skippedAfterPriorFailure", true)
        check(count < 16) { "Frame timeline snapshot bound reached" }
        val index = count++
        val prefix = "${index.toString().padStart(2, '0')}-$label"
        val transcript = File(directory, "$prefix.qmp.jsonl")
        val receipt = File(directory, "$prefix.json")
        var receiptCreated = false
        try {
            check(transcript.createNewFile())
            receiptCreated = receipt.createNewFile()
            check(receiptCreated)
            check(status.getInt("pid") == workerPid && status.getInt("uid") == workerUid)
            // Android may alias /data/user/0 and /data/data. Resolve both
            // roots, then bind the connected endpoint to the exact worker.
            val endpoint = File(requireNotNull(status.getString("debugQmpPath"))).canonicalFile
            check(endpoint.name == "qmp-$workerPid.sock" && endpoint.parentFile?.name == "work")
            check(endpoint.path.startsWith(filesDir.canonicalPath + "/"))
            val observed = requireNotNull(status.getBundle("observations"))
            val counters = counterNames.filter { observed.containsKey(it) }.associateWith { observed.getLong(it) }
            result.put("nativeCounters", JSONObject(counters)).put("nativeCountersSampledBeforeQmp", true)
            val nativeDeltas = JSONObject()
            previousCounters?.let { old ->
                for (name in counterNames) {
                    val before = old[name]; val now = counters[name]
                    nativeDeltas.put(name, if (before != null && now != null && now >= before) now - before else JSONObject.NULL)
                }
            }
            result.put("nativeCounterDeltas", nativeDeltas)
            val deadline = clock.millis() + 1_000
            LocalSocket().use { socket ->
                FrameTimelineEvidence.ClosingDeadline(socket, clock, deadline).use {
                    socket.connect(LocalSocketAddress(endpoint.path, LocalSocketAddress.Namespace.FILESYSTEM))
                    socket.soTimeout = 1_000
                    val peer = socket.peerCredentials
                    check(peer.pid == workerPid && peer.uid == workerUid) { "Frame timeline endpoint identity changed" }
                    val input = BufferedInputStream(socket.inputStream)
                    var totalBytes = 0
                    fun response(): JSONObject {
                        val line = FrameTimelineEvidence.readLine(input, clock, deadline)
                        val bytes = (line + "\n").toByteArray(Charsets.UTF_8)
                        totalBytes += bytes.size
                        check(totalBytes <= 1_048_576) { "Frame diagnostic transcript bound reached" }
                        transcript.appendBytes(bytes)
                        return JSONObject(line)
                    }
                    check(response().has("QMP")) { "Frame timeline QMP greeting missing" }
                    var requestNumber = 0
                    fun command(name: String, arguments: JSONObject? = null): JSONObject {
                        FrameTimelineEvidence.beforeDeadline(clock, deadline)
                        val id = "lt-frame-${requestNumber++}"
                        val request = JSONObject().put("execute", name).put("id", id)
                        arguments?.let { request.put("arguments", it) }
                        socket.outputStream.write((request.toString() + "\n").toByteArray(Charsets.UTF_8))
                        repeat(9) {
                            val reply = response()
                            if (reply.has("id")) {
                                check(reply.getString("id") == id) { "Unexpected frame diagnostic response ID" }
                                check(!reply.has("error")) { "Read-only frame diagnostic failed: $reply" }
                                return reply
                            }
                            check(reply.has("event")) { "Invalid asynchronous QMP response" }
                        }
                        error("Frame diagnostic event bound reached")
                    }
                    command("qmp_capabilities")
                    val children = command("qom-list", JSONObject().put("path", "/machine/unattached")).getJSONArray("return")
                    check(children.length() <= 256)
                    val lcd = (0 until children.length()).map { children.getJSONObject(it) }
                        .filter { it.getString("type") == "child<ipodtouch.lcd>" }
                    check(lcd.size == 1) { "Expected one existing n72 LCD object" }
                    val child = lcd.single().getString("name")
                    check(child.matches(Regex("[A-Za-z0-9_.\\[\\]-]{1,64}")) && child !in listOf(".", ".."))
                    val path = "/machine/unattached/$child"
                    val properties = command("qom-list", JSONObject().put("path", path)).getJSONArray("return")
                    check(properties.length() <= 128)
                    check((0 until properties.length()).any { properties.getJSONObject(it).getString("name") == "frame-timeline" })
                    val text = command("qom-get", JSONObject().put("path", path).put("property", "frame-timeline")).getString("return")
                    FrameTimelineEvidence.beforeDeadline(clock, deadline)
                    val timeline = FrameTimelineEvidence.parse(text)
                    File(directory, "$prefix.timeline.txt").writeText(text)
                    result.put("lcdPath", path).put("timelineEntries", timeline.size())
                        .put("timelineAvailable", true).put("qmpRequests", requestNumber)
                        .put("transcriptBytes", totalBytes)
                    if (timeline.size() > 0) result.put("lastVblankSequence", timeline.sequence.last())
                        .put("lastGuestVirtualNs", timeline.virtualNs.last())
                    previousTimeline?.let { before ->
                        val delta = FrameTimelineEvidence.compare(before, timeline)
                        result.put("timelineContinuityVerified", delta.comparable).put("timelineComparisonReason", delta.reason)
                            .put("vblankPeriodsDelta", if (delta.comparable) delta.periods else JSONObject.NULL)
                            .put("newScanoutBaseLatchesDelta", if (delta.comparable) delta.newBaseLatches else JSONObject.NULL)
                    }
                    // Base changes are not pixel changes: the guest can modify
                    // pixels in place or flip between identical buffers.
                    result.put("scanoutBaseChangesArePixelChanges", false)
                    FrameTimelineEvidence.beforeDeadline(clock, deadline)
                    previousTimeline = timeline; previousCounters = counters
                }
            }
            result.put("pass", true)
        } catch (failure: Exception) {
            failed = true
            result.put("error", failure.toString()).put("noAutomaticRetry", true)
        } finally {
            result.put("hostElapsedEndMs", clock.millis())
            if (receiptCreated) receipt.writeText(result.toString(2))
        }
        return result
    }
}

package org.un1box.android

import android.content.ComponentName
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.DeadObjectException
import android.os.IBinder
import android.os.RemoteException
import android.provider.MediaStore
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.InputStream
import java.security.MessageDigest
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.zip.ZipEntry
import java.util.zip.ZipInputStream
import kotlin.text.isEmpty
import kotlin.text.isNotEmpty

/**
 * The Web UI's backend: every request the page makes, answered by driving the same binder
 * interfaces the activity drives. Nothing here invents a second way to do anything — the guest
 * is the engine worker's, the preparations are the firmware worker's, installs go through the
 * one process-wide [StoreInstaller] the app uses, and settings are the same SharedPreferences the
 * app's screens read and write — so what the Web UI does is exactly what the app does.
 *
 * The one piece of its own is the status poll: one thread, once a second, asking the engine
 * worker for its state (the device library along with it), exactly as the activity's poll does,
 * so every browser client reads a cache instead of stampeding the worker.
 */
private fun WebUiRequest.param(name: String, default: String = ""): String = query[name] ?: default

internal class WebUi(private val context: Context) {
    private val app = context.applicationContext
    private val installer = StoreInstaller.of(app)
    private val guestApps = GuestApps(app)

    // The engine worker, its status and the device library, all kept by the poll thread.
    @Volatile private var worker: IEngine? = null
    @Volatile private var status: Bundle = stoppedStatus("")
    @Volatile private var records: List<Bundle> = emptyList()
    @Volatile private var expectedExit = false
    @Volatile private var startBusy = false
    @Volatile private var pendingStart: Pair<String, Boolean>? = null
    private var bound = false

    // The firmware worker, for preparation from an IPSW.
    @Volatile private var firmware: IFirmware? = null
    private var firmwareBound = false
    @Volatile private var firmwareRestarting = false
    @Volatile private var pendingFirmwareStart = false
    @Volatile private var firmwareExpectedExit = false

    // The device-file import (an .itbase or a prepared folder) the page uploaded.
    @Volatile private var importing = false
    @Volatile private var importPhase = ""
    @Volatile private var importProgress = -1f

    // What an operation that runs in the background wants to tell the page.
    @Volatile private var opNotice = ""
    @Volatile private var opError = ""

    /** The prepare-from-IPSW flow's choices, the Web UI's own copy of the app's PrepareState. */
    private class Prepare {
        @Volatile var firmwareId = ""
        @Volatile var ipswPath = ""
        @Volatile var ipswFor = ""
        @Volatile var ipswError = ""
        @Volatile var romPath = ""
        @Volatile var romFor: Bootrom? = null
        @Volatile var romLabel = ""
        @Volatile var romChecking = false
        @Volatile var romError = ""
        @Volatile var preparation: Bundle? = null
        @Volatile var importing = false
        @Volatile var importPhase = ""
        @Volatile var importProgress = -1f
        @Volatile var importError = ""
        @Volatile var importedDestination = ""
        @Volatile var imported = false
    }

    private val prep = Prepare()

    // The running guest's own device report, asked once per guest like the store's model.
    @Volatile private var exactTarget: Pair<String, StoreTarget>? = null

    private val poll = Executors.newSingleThreadScheduledExecutor { task ->
        Thread(task, "webui-poll").apply { isDaemon = true }
    }
    private val buttonCalls = Executors.newSingleThreadExecutor { task ->
        Thread(task, "webui-buttons").apply { isDaemon = true }
    }
    @Volatile private var page: ByteArray? = null
    @Volatile private var catalogCache: List<CatalogFirmware>? = null

    // -------------------------------------------------------------------------------- lifecycle

    fun start() {
        bindWorker()
        poll.scheduleWithFixedDelay(::tick, 100, 1000, TimeUnit.MILLISECONDS)
    }

    fun close() {
        poll.shutdownNow()
        buttonCalls.shutdownNow()
        unbindWorker()
        releaseFirmwareWorker()
    }

    /** The one thread that keeps the caches fresh and drives the prepare/import state machines. */
    private fun tick() {
        val observed = worker
        if (observed != null) {
            try {
                status = observed.status()
                val socket = if (status.getString("state") == "running") status.getString("usbMuxSocket") ?: "" else ""
                installer.socket = socket.ifEmpty { null }
            } catch (_: RemoteException) { workerExited() }
            try { records = worker?.records()?.toList() ?: records } catch (_: RemoteException) { }
        }
        if (importing || prep.importing) {
            val progress = try { worker?.importProgress() } catch (_: RemoteException) { null }
            if (progress != null) {
                val text = importPhaseText(progress.getString("phase") ?: "")
                val total = progress.getLong("total")
                val fraction = if (total > 0) progress.getLong("done").toFloat() / total else -1f
                if (importing) { importPhase = text; importProgress = fraction }
                if (prep.importing) { prep.importPhase = text; prep.importProgress = fraction }
            }
        }
        val active = firmware
        if (active != null && !firmwareRestarting) {
            try {
                val state = active.status()
                if (state.getString("state") != "idle" && state.getString("destination") != prep.importedDestination)
                    prep.preparation = state
                if (state.getString("state") == "complete" && !prep.importing &&
                    state.getString("destination") != prep.importedDestination) importPreparedOutput(state)
            } catch (_: RemoteException) { }
        }
    }

    // ---------------------------------------------------------------------------------- routing

    fun route(request: WebUiRequest): WebUiResponse {
        val path = request.path
        val post = request.method == "POST"
        return when {
            request.method == "GET" && (path == "/" || path == "/index.html") -> pageIndex()
            path == "/api/status" -> statusJson()
            path == "/api/ack" -> { opNotice = ""; opError = ""; ok("") }

            request.method == "GET" && path == "/api/devices" -> devicesJson()
            post && path == "/api/devices/select" -> { val body = request.json(); selectDevice(body.optString("id")) }
            post && path == "/api/devices/rename" -> { val body = request.json(); renameDevice(body.optString("id"), body.optString("name")) }
            post && path == "/api/devices/delete" -> { val body = request.json(); deleteDevice(body.optString("id")) }
            post && path == "/api/devices/import" -> importUpload(request)
            post && path == "/api/devices/rom" -> romUpload(request)

            post && path == "/api/guest/start" -> { val body = request.json(); startGuest(body.optString("id").ifEmpty { selectedId() }, body.optBoolean("recover")) }
            post && path == "/api/guest/stop" -> stopGuest()
            post && path == "/api/guest/pause" -> { val body = request.json(); pauseGuest(body.optBoolean("paused", true)) }
            post && path == "/api/guest/button" -> { val body = request.json(); sendButton(body.optInt("id"), body.optBoolean("down", true)) }
            post && path == "/api/guest/pulse" -> { val body = request.json(); pulse(body.optInt("id")) }
            post && path == "/api/guest/touch" -> touchGuest(request.json())
            post && path == "/api/guest/cancel-input" -> { engine().cancelInput(); ok("") }
            request.method == "GET" && path == "/api/guest/screen.png" -> screenPng()
            post && path == "/api/guest/screenshot" -> screenshotToPictures()
            post && path == "/api/guest/profile" -> { val body = request.json(); profile(body.optBoolean("recording", true)) }
            request.method == "GET" && path == "/api/guest/frontmost" -> frontmost()
            post && path == "/api/guest/hitch" -> hitchSnapshot()

            request.method == "GET" && path == "/api/prepare/catalog" -> prepareCatalog()
            request.method == "GET" && path == "/api/prepare/state" -> prepareState()
            post && path == "/api/prepare/ipsw/download" -> { val body = request.json(); ipswDownload(body.optString("firmwareId")) }
            post && path == "/api/prepare/ipsw/upload" -> ipswUpload(request)
            post && path == "/api/prepare/rom/download" -> { val body = request.json(); romDownload(body.optString("firmwareId")) }
            post && path == "/api/prepare/rom/upload" -> romUploadPrepare(request)
            post && path == "/api/prepare/start" -> { val body = request.json(); startPreparation(body.optString("firmwareId")) }
            post && path == "/api/prepare/cancel" -> { try { firmware?.cancel() } catch (_: RemoteException) { }; ok("Cancelling") }
            post && path == "/api/prepare/import-retry" -> retryPreparedImport()

            request.method == "GET" && path == "/api/store/refresh" -> storeRefresh()
            request.method == "GET" && path == "/api/store/search" -> storeSearch(request)
            request.method == "GET" && path == "/api/store/featured" -> storeListing { LegacyStore.collection("featured") }
            request.method == "GET" && path == "/api/store/genres" -> { WebUiResponse.json(JSONObject().put("genres", LegacyStore.genres().map { JSONObject().put("id", it.id).put("name", it.name).put("count", it.count) })) }
            request.method == "GET" && path == "/api/store/genre" -> storeListing { LegacyStore.genreApps(request.param("id"), request.param("sort", "versions")) }
            request.method == "GET" && path == "/api/store/collections" -> { WebUiResponse.json(JSONObject().put("collections", LegacyStore.collections().map { JSONObject().put("slug", it.slug).put("title", it.title).put("subtitle", it.subtitle).put("count", it.count) })) }
            request.method == "GET" && path == "/api/store/collection" -> storeListing { LegacyStore.collection(request.param("slug")) }
            request.method == "GET" && path == "/api/store/charts" -> storeChart(request)
            request.method == "GET" && path == "/api/store/page" -> storeListing { LegacyStore.page(request.param("url")) }
            post && path == "/api/store/app" -> storeApp(request.json())
            post && path == "/api/store/install" -> storeInstall(request.json())
            post && path == "/api/store/ipa" -> ipaUpload(request)
            request.method == "GET" && path == "/api/store/crash" -> crashReport()

            request.method == "GET" && path == "/api/queue" -> queueJson()
            post && path == "/api/queue/cancel" -> { val body = request.json(); installer.queue.cancel(body.optLong("id")); ok("") }
            post && path == "/api/queue/retry" -> { val body = request.json(); installer.queue.retry(body.optLong("id")); ok("") }
            post && path == "/api/queue/dismiss" -> { val body = request.json(); installer.queue.dismiss(body.optLong("id")); ok("") }
            post && path == "/api/queue/clear" -> { installer.queue.clearFinished(); ok("") }

            request.method == "GET" && path == "/api/settings" -> settingsJson()
            post && path == "/api/settings" -> settingsPost(request.json())
            post && path == "/api/webui/stop" -> { Thread({ Thread.sleep(400); WebUiService.setEnabled(app, false) }, "webui-self-stop").start(); ok("The Web UI is stopping") }

            else -> throw WebUiError(404, "No such page: $path")
        }
    }

    private fun ok(message: String): WebUiResponse = WebUiResponse.json(JSONObject().put("result", message))

    private fun pageIndex(): WebUiResponse {
        val cached = page ?: app.assets.open("webui/index.html").use { it.readBytes() }.also { page = it }
        return WebUiResponse.bytes("text/html; charset=utf-8", cached)
    }

    private fun hex(digest: MessageDigest) = digest.digest().joinToString("") { "%02x".format(it) }

    // ----------------------------------------------------------------------------- the engine link

    private fun engine(): IEngine = worker ?: run {
        bindWorker()
        throw WebUiError(503, "The device worker is starting; try again in a moment")
    }

    private fun bindWorker() {
        synchronized(this) {
            if (bound) return
            bound = app.bindService(Intent(app, EngineService::class.java), engineConnection, Context.BIND_AUTO_CREATE)
        }
    }

    private fun unbindWorker() {
        synchronized(this) {
            if (!bound) return
            runCatching { app.unbindService(engineConnection) }
            bound = false
        }
        worker = null
    }

    private val engineConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName, binder: IBinder) {
            val next = IEngine.Stub.asInterface(binder)
            binder.linkToDeath({ workerExited() }, 0)
            worker = next
            val request = pendingStart
            if (request != null) { pendingStart = null; startGuest(request.first, request.second) }
        }

        override fun onServiceDisconnected(name: ComponentName) { }
    }

    /** The worker's process is gone: its death was expected while stopping, surprising otherwise. */
    private fun workerExited() {
        val expected = expectedExit
        expectedExit = false
        worker = null
        status = stoppedStatus(if (expected) "" else
            "The guest worker exited; its writable state is kept. Start creates a fresh worker.")
        unbindWorker()
    }

    private fun stoppedStatus(error: String) = Bundle().apply {
        putString("state", if (error.isEmpty()) "stopped" else "failed")
        putString("detail", ""); putString("error", error); putBoolean("sessionStarted", false)
    }

    // ------------------------------------------------------------------------------ status and app

    private fun statusJson(): WebUiResponse {
        val value = JSONObject()
            .put("engine", bundleToJson(status))
            .put("engineAlive", worker != null)
            .put("displayAsleep", status.getBundle("observations")?.let {
                it.getBoolean("runtimeDiagnosticsAvailable") && it.getLong("engineReady") != 0L && it.getLong("displaySleeping") != 0L
            } ?: false)
            .put("devices", JSONArray().putAllJson(records.map { recordJson(it) }))
            .put("queue", JSONArray().putAllJson(installer.queue.snapshot().map { queueJson(it) }))
            .put("import", JSONObject().put("active", importing).put("phase", importPhase).put("progress", importProgress.toDouble()))
            .put("prepare", prepareJson())
            .put("installer", JSONObject().put("available", installer.available).put("socket", installer.socket ?: ""))
            .put("notice", opNotice).put("error", opError)
            .put("app", JSONObject().put("version", BuildConfig.VERSION_NAME).put("debug", BuildConfig.DEBUG))
        return WebUiResponse.json(value)
    }

    private fun recordJson(record: Bundle): JSONObject {
        val target = firmwareTargets()["${record.getString("board")}-${record.getString("build")}"]
        return JSONObject()
            .put("id", record.getString("id") ?: "")
            .put("name", record.getString("name") ?: "")
            .put("board", record.getString("board") ?: "").put("build", record.getString("build") ?: "")
            .put("selected", record.getBoolean("selected")).put("dirty", record.getBoolean("dirty"))
            .put("contacts", record.getString("contacts") ?: "1")
            .put("acceleration", record.getBoolean("acceleration")).put("gyro", record.getBoolean("gyro"))
            .put("bootromAvailable", record.getBoolean("bootromAvailable"))
            .put("guestPackageAvailable", record.getBoolean("guestPackageAvailable"))
            .put("model", target?.let { shortProductName(it.productType) } ?: record.getString("board"))
            .put("subtitle", target?.let { "${shortProductName(it.productType)} · iOS ${it.ios}" }
                ?: "${record.getString("board")} · ${record.getString("build")}")
    }

    private fun queueJson(item: QueueItem) = JSONObject()
        .put("id", item.id).put("key", item.key).put("name", item.name)
        .put("state", item.state.name).put("phase", item.phase)
        .put("progress", item.progress?.toDouble() ?: JSONObject.NULL)
        .put("overall", item.overall.toDouble()).put("committed", item.committed)
        .put("active", item.active).put("cancellable", item.cancellable)
        .put("error", item.error ?: "")

    private fun queueJson(): WebUiResponse = WebUiResponse.json(JSONObject()
        .put("queue", JSONArray().putAllJson(installer.queue.snapshot().map { queueJson(it) })))

    private fun JSONArray.putAllJson(items: List<JSONObject>): JSONArray {
        for (item in items) put(item)
        return this
    }

    private fun firmwares(): List<CatalogFirmware> = catalogCache ?: synchronized(this) {
        catalogCache ?: FirmwareCatalog.load(app).also { catalogCache = it }
    }

    private fun firmwareTargets(): Map<String, StoreTarget> =
        firmwares().associate { "${it.board}-${it.build}" to StoreTarget.of(it.productType, it.version) }

    private fun selectedId(): String = records.firstOrNull { it.getBoolean("selected") }?.getString("id") ?: ""

    private fun deviceNames(): List<String> = records.mapNotNull { it.getString("name") }

    private fun importPhaseText(phase: String) = when (phase) {
        "hashing" -> "Checking the file…"
        "extracting" -> "Unpacking the iOS system…"
        "packing" -> "Packing the iOS storage…"
        "verifying" -> "Checking the iOS system…"
        else -> "Setting up the iOS system…"
    }

    // ------------------------------------------------------------------------------- guest control

    private fun startGuest(recordId: String, cold: Boolean): WebUiResponse {
        if (recordId.isEmpty()) throw WebUiError(400, "Select a device to start")
        if (startBusy) throw WebUiError(409, "A start is already in progress")
        val observed = worker
        if (observed == null) {
            pendingStart = recordId to cold
            bindWorker()
            return ok("Starting the device worker…")
        }
        if (status.getBoolean("sessionStarted")) { restartWorkerThenStart(recordId, cold); return ok("Restarting the guest worker…") }
        performStart(observed, recordId, cold)
        return ok(if (cold) "Recovering…" else "Starting…")
    }

    private fun performStart(engine: IEngine, recordId: String, cold: Boolean) {
        expectedExit = false
        app.startForegroundService(Intent(app, EngineService::class.java))
        Thread({
            try {
                // Newer bundled guest tools reach an existing device here, as they do in the app.
                try { GuestTools.newerFor(app, recordId)?.let { engine.setGuestPackage(recordId, it.absolutePath) } }
                catch (failure: Exception) { android.util.Log.w("Un1box", "Web UI guest tools not updated", failure) }
                status = if (cold) engine.recoverStart(recordId) else engine.start(recordId)
            } catch (failure: Exception) {
                opError = "Guest start failed: ${failure.message ?: "worker exited"}. Writable state and diagnostics retained."
            }
        }, "webui-guest-start").start()
    }

    /** A worker that has already run a session refuses a second one: stop it, wait, and start over. */
    private fun restartWorkerThenStart(recordId: String, cold: Boolean) {
        startBusy = true
        expectedExit = true
        pendingStart = recordId to cold
        val old = worker
        unbindWorker()
        val binder = old?.asBinder() ?: run { startBusy = false; bindWorker(); return }
        Thread({
            val died = CountDownLatch(1)
            val recipient = IBinder.DeathRecipient { died.countDown() }
            try {
                if (binder.isBinderAlive) {
                    try { binder.linkToDeath(recipient, 0) } catch (_: DeadObjectException) { died.countDown() }
                    try { old.stop() } catch (_: DeadObjectException) { died.countDown() }
                    if (!binder.isBinderAlive || died.await(45, TimeUnit.SECONDS)) bindWorker()
                    else { pendingStart = null; opError = "The previous guest worker has not exited; try again after it stops." }
                } else bindWorker()
            } catch (failure: Exception) {
                pendingStart = null
                opError = "Guest worker restart failed: ${failure.message ?: "unknown error"}"
            } finally {
                try { binder.unlinkToDeath(recipient, 0) } catch (_: java.util.NoSuchElementException) { }
                startBusy = false
            }
        }, "webui-worker-restart").start()
    }

    private fun stopGuest(): WebUiResponse {
        val engine = engine()
        expectedExit = true
        Thread({ try { engine.stop() } catch (failure: RemoteException) { opError = "Stop failed: ${failure.message}" } }, "webui-guest-stop").start()
        return ok("Shutting down…")
    }

    private fun pauseGuest(paused: Boolean): WebUiResponse {
        try { engine().pause(paused) } catch (failure: RemoteException) { throw WebUiError(503, "Guest worker unavailable") }
        return ok(if (paused) "Paused" else "Resumed")
    }

    private fun sendButton(id: Int, down: Boolean): WebUiResponse {
        if (id !in 0..3) throw WebUiError(400, "Unknown button $id")
        val engine = engine()
        buttonCalls.execute {
            try { val result: String = engine.button(id, down); if (result.isNotEmpty()) opError = result } catch (_: RemoteException) { }
        }
        return ok("")
    }

    private fun pulse(id: Int): WebUiResponse {
        if (id !in 0..3) throw WebUiError(400, "Unknown button $id")
        val engine = engine()
        buttonCalls.execute {
            try { engine.button(id, true); Thread.sleep(100); engine.button(id, false) } catch (_: RemoteException) { }
        }
        return ok("")
    }

    /** One batch of touches, in guest pixels: the page maps its canvas onto the frame it displays. */
    private fun touchGuest(body: JSONObject): WebUiResponse {
        val engine = engine()
        if (status.getString("state") != "running") throw WebUiError(409, "The guest is not running")
        val contacts = body.optJSONArray("contacts") ?: throw WebUiError(400, "No contacts in the touch request")
        val count = contacts.length()
        if (count < 1 || count > 8) throw WebUiError(400, "Between one and eight contacts at a time")
        val ids = IntArray(count); val phases = IntArray(count); val xy = FloatArray(count * 2)
        for (index in 0 until count) {
            val contact = contacts.optJSONObject(index) ?: throw WebUiError(400, "A contact is missing")
            ids[index] = index
            phases[index] = when (contact.optString("phase", "move")) { "down" -> 0; "move" -> 1; "up" -> 2; else -> 1 }
            xy[index * 2] = contact.optDouble("x", -1.0).toFloat()
            xy[index * 2 + 1] = contact.optDouble("y", -1.0).toFloat()
        }
        val result = try {
            engine.touch(System.nanoTime(), ids, phases, xy, body.optInt("width"), body.optInt("height"), 0)
        } catch (failure: RemoteException) { throw WebUiError(503, "Guest worker unavailable") }
        if (result.isNotEmpty()) throw WebUiError(409, result)
        return ok("")
    }

    private fun screenPng(): WebUiResponse {
        val engine = engine()
        val directory = File(app.filesDir, "screenshots").apply { mkdirs() }
        val file = File(directory, "web-${System.nanoTime()}.png")
        try {
            val error = try { engine.screenshot(file.absolutePath) } catch (failure: RemoteException) { "Guest worker unavailable" }
            if (error.isNotEmpty()) throw WebUiError(409, error)
            return WebUiResponse.bytes("image/png", file.readBytes())
        } finally { file.delete() }
    }

    private fun screenshotToPictures(): WebUiResponse {
        val path = File(app.filesDir, "screenshots/guest-${System.currentTimeMillis()}.png").absolutePath
        val error = try { engine().screenshot(path) } catch (failure: RemoteException) { "Guest worker unavailable" }
        if (error.isNotEmpty()) throw WebUiError(409, error)
        val values = ContentValues().apply {
            put(MediaStore.Images.Media.DISPLAY_NAME, File(path).name)
            put(MediaStore.Images.Media.MIME_TYPE, "image/png")
            put(MediaStore.Images.Media.RELATIVE_PATH, "Pictures/Un1box")
            put(MediaStore.Images.Media.IS_PENDING, 1)
        }
        val uri = app.contentResolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values)
            ?: throw WebUiError(500, "Cannot create the screenshot")
        try {
            app.contentResolver.openOutputStream(uri)?.use { output -> File(path).inputStream().use { it.copyTo(output) } }
                ?: throw WebUiError(500, "Cannot write the screenshot")
            values.clear(); values.put(MediaStore.Images.Media.IS_PENDING, 0)
            app.contentResolver.update(uri, values, null, null)
        } catch (failure: Exception) {
            app.contentResolver.delete(uri, null, null)
            throw WebUiError(500, "Screenshot failed: ${failure.message}")
        }
        return ok("Screenshot saved to Pictures/Un1box")
    }

    private fun profile(recording: Boolean): WebUiResponse {
        val result = try { engine().profile(recording) } catch (failure: RemoteException) { throw WebUiError(503, "Guest worker unavailable") }
        return WebUiResponse.json(bundleToJson(result))
    }

    private fun frontmost(): WebUiResponse {
        val result = try { engine().frontmostApp() } catch (failure: RemoteException) { throw WebUiError(503, "Guest worker unavailable") }
        return WebUiResponse.json(bundleToJson(result))
    }

    /** The hitch inspector's evidence: pause, then freeze the frame timeline and keep the capture. */
    private fun hitchSnapshot(): WebUiResponse {
        val engine = engine()
        if (status.getString("state") !in listOf("running", "paused")) throw WebUiError(409, "The guest is not running")
        val wasRunning = status.getString("state") == "running"
        if (wasRunning) try { engine.pause(true) } catch (_: RemoteException) { }
        val snapshot = try { engine.hitchSnapshot() } catch (failure: RemoteException) { throw WebUiError(503, "Guest worker unavailable") }
        val frames = JSONArray()
        try {
            val file = File(snapshot.getString("directory") ?: "", "frames.tsv")
            if (file.isFile) file.readLines().drop(1).takeLast(600).forEach { line ->
                val numbers = line.split(' ').mapNotNull { it.toLongOrNull() }
                if (numbers.size == 4) frames.put(JSONArray(numbers))
            }
        } catch (_: Exception) { }
        return WebUiResponse.json(bundleToJson(snapshot)
            .put("paused", wasRunning)
            .put("frames", frames))
    }

    // -------------------------------------------------------------------------- the device library

    private fun devicesJson(): WebUiResponse =
        WebUiResponse.json(JSONObject().put("devices", JSONArray().putAllJson(records.map { recordJson(it) })))

    private fun selectDevice(id: String): WebUiResponse {
        if (id.isEmpty()) throw WebUiError(400, "No device was named")
        val result = try { engine().select(id) } catch (failure: RemoteException) { throw WebUiError(503, "Device worker unavailable") }
        if (result.isNotEmpty()) throw WebUiError(409, result)
        return ok("Selected")
    }

    private fun renameDevice(id: String, name: String): WebUiResponse {
        if (id.isEmpty()) throw WebUiError(400, "No device was named")
        if (!fitsDeviceName(name)) throw WebUiError(400, "A device name is at most 60 characters (127 bytes)")
        val result = try { engine().rename(id, name.trim()) } catch (failure: RemoteException) { throw WebUiError(503, "Device worker unavailable") }
        if (result.isNotEmpty()) throw WebUiError(409, result)
        return ok("Renamed to ${name.trim()}")
    }

    private fun deleteDevice(id: String): WebUiResponse {
        if (id.isEmpty()) throw WebUiError(400, "No device was named")
        val result = try { engine().remove(id) } catch (failure: RemoteException) { throw WebUiError(503, "Device worker unavailable") }
        if (result.isNotEmpty()) throw WebUiError(409, result)
        return ok("Device deleted")
    }

    /** A boot ROM the import can start the device from; kept as one upload, like the wizard's picker. */
    private fun romUpload(request: WebUiRequest): WebUiResponse {
        if (request.contentLength !in 1..(1L shl 20)) throw WebUiError(400, "A boot ROM is one small file")
        val target = File(app.filesDir, "webui-bootrom.bin")
        target.writeBytes(request.readBody(1L shl 20))
        return WebUiResponse.json(JSONObject().put("result", "Boot ROM kept for this import").put("path", target.absolutePath))
    }

    /**
     * Device files the page sent: a `.itbase` archive (the portable prepared system), or a ZIP that
     * holds either one, or a prepared folder (device.lock.json, or index.json and files/). The
     * upload is streamed to disk here — only the import itself runs in the background, reporting
     * its progress through the status poll like the app's imports do.
     */
    private fun importUpload(request: WebUiRequest): WebUiResponse {
        if (importing) throw WebUiError(409, "An import is already running")
        val name = request.param("name").trim()
        val rom = request.param("rom")
        if (rom.isNotEmpty() && !File(rom).isFile) throw WebUiError(400, "No such boot ROM was uploaded")
        if (request.contentLength <= 0) throw WebUiError(400, "Send the device files as the request body")
        val staging = File(app.filesDir, "webui-import-${System.nanoTime()}").apply { mkdirs() }
        val received = receiveDeviceFiles(staging, request.bodyStream())
        importing = true
        importPhase = "Setting up the iOS system…"
        importProgress = -1f
        val before = records.mapNotNull { it.getString("id") }.toSet()
        Thread({
            val engine = worker ?: run {
                opError = "The device worker is unavailable; try the import again"
                staging.deleteRecursively(); importing = false
                return@Thread
            }
            val result: String = try {
                engine.importDevice(received.first.absolutePath, name, rom, "", received.second)
            } catch (failure: Exception) { failure.message ?: "The import failed" }
            if (result.isEmpty()) {
                try {
                    val created = engine.records().firstOrNull { it.getString("id") !in before }
                    if (created != null) {
                        val tools = GuestTools.forDevice(app, created.getString("board") ?: "", created.getString("build") ?: "")
                        tools?.let { engine.setGuestPackage(created.getString("id"), it.absolutePath) }
                        val shown = name.ifEmpty { defaultDeviceName(created.getString("board") ?: "", created.getString("build") ?: "",
                            firmwareTargets(), deviceNames() - (created.getString("name") ?: "")) }
                        engine.rename(created.getString("id"), shown)
                        if (status.getString("state") !in listOf("running", "paused", "starting", "stopping"))
                            engine.select(created.getString("id"))
                        opNotice = "Added $shown"
                    }
                } catch (failure: Exception) {
                    opError = "Added the device, but could not finish setting it up: ${failure.message}"
                }
            } else opError = result
            staging.deleteRecursively()
            importing = false
        }, "webui-device-import").start()
        return ok("Importing${if (name.isEmpty()) "" else " $name"}…")
    }

    /** One uploaded body as either an archive (with its SHA-256) or an unpacked folder. */
    private fun receiveDeviceFiles(staging: File, body: InputStream): Pair<File, String> {
        // Sniff the ZIP magic without eating it: the archive reader needs the whole stream.
        val source = java.io.PushbackInputStream(body.buffered(1 shl 16), 4)
        val head = ByteArray(4)
        var read = 0
        while (read < head.size) {
            val n = source.read(head, read, head.size - read)
            if (n < 0) break
            read += n
        }
        val archive = File(staging, "base.itbase")
        val digest = MessageDigest.getInstance("SHA-256")
        val zip = read == 4 && head[0] == 'P'.code.toByte() && head[1] == 'K'.code.toByte()
        if (!zip) {
            archive.outputStream().use { output ->
                if (read > 0) { output.write(head, 0, read); digest.update(head, 0, read) }
                val block = ByteArray(1 shl 16)
                while (true) {
                    val n = source.read(block)
                    if (n < 0) break
                    output.write(block, 0, n); digest.update(block, 0, n)
                }
            }
            return archive to hex(digest)
        }
        source.unread(head, 0, read)
        // A ZIP: either a folder of a prepared system, or a .itbase inside one.
        val unpacked = File(staging, "unpacked").apply { mkdirs() }
        var bytes = 0L; var files = 0
        val names = mutableListOf<String>()
        var inside: String? = null
        val insideDigest = MessageDigest.getInstance("SHA-256")
        ZipInputStream(source).use { zipStream ->
            while (true) {
                val entry: ZipEntry = zipStream.nextEntry ?: break
                val name = entry.name
                if (name.isEmpty() || name.startsWith("/") || name.contains("..")) throw WebUiError(400, "The ZIP holds an unusable entry")
                names += name
                if (++files > 20_000) throw WebUiError(400, "The ZIP holds too many files")
                if (entry.isDirectory) { File(unpacked, name).mkdirs(); continue }
                bytes += entry.size
                if (bytes > 8L shl 30) throw WebUiError(413, "The ZIP is too large to unpack")
                val target = File(unpacked, name)
                target.parentFile?.mkdirs()
                target.outputStream().use { output ->
                    val block = ByteArray(1 shl 16)
                    while (true) {
                        val n = zipStream.read(block)
                        if (n < 0) break
                        output.write(block, 0, n)
                        if (name.endsWith(".itbase", true)) insideDigest.update(block, 0, n)
                    }
                }
                if (name.endsWith(".itbase", true) && inside == null) inside = target.absolutePath
            }
        }
        if (inside != null) return File(inside!!) to hex(insideDigest)
        // A folder: the prepared root is the one holding the lock (or index.json and files/).
        fun holds(files: List<String>, marker: String) = files.firstOrNull { it == marker || it.endsWith("/$marker") }
        val root = holds(names, "device.lock.json")?.substringBeforeLast('/')
            ?: holds(names, "index.json")?.takeIf { names.any { it.startsWith("${it.substringBeforeLast('/')}/files/") } }?.substringBeforeLast('/')
            ?: throw WebUiError(400, "The upload holds no prepared iOS system (.itbase or a prepared folder)")
        return File(unpacked, root) to ""
    }

    // ---------------------------------------------------------------- preparation from an IPSW

    private fun prepareCatalog(): WebUiResponse {
        val supported = File(app.applicationInfo.nativeLibraryDir, "libfirmwarekit.so").canExecute()
        val firmwares = JSONArray()
        for (entry in firmwares()) firmwares.put(JSONObject()
            .put("id", entry.id).put("board", entry.board).put("productType", entry.productType)
            .put("version", entry.version).put("build", entry.build).put("label", entry.label)
            .put("released", entry.released).put("prerelease", entry.prerelease)
            .put("support", entry.support.label).put("supportDetail", entry.support.detail).put("tested", entry.support.tested)
            .put("bytes", entry.bytes).put("sha1", entry.sha1)
            .put("needsRom", needsBootrom(entry.board))
            .put("supported", supported)
            .put("recommended", entry.id == recommendedFirmware))
        return WebUiResponse.json(JSONObject().put("firmwares", firmwares).put("supported", supported))
    }

    private fun prepareJson(): JSONObject {
        val chosen = firmwares().firstOrNull { it.id == prep.firmwareId }
        val ipsw = JSONObject()
            .put("forFirmware", prep.ipswFor).put("ready", prep.ipswPath.isNotEmpty() && prep.ipswFor == prep.firmwareId)
            .put("error", prep.ipswError)
            .put("downloading", IpswDownload.running)
            .put("downloadFirmware", IpswDownload.firmwareId)
            .put("downloadProgress", IpswDownload.progress.toDouble())
            .put("status", IpswDownload.status)
        val rom = chosen?.let { Bootrom.forBoard(it.board) }
        return JSONObject()
            .put("firmwareId", prep.firmwareId)
            .put("ipsw", ipsw)
            .put("rom", JSONObject()
                .put("needed", rom != null).put("ready", rom != null && prep.romPath.isNotEmpty() && prep.romFor == rom)
                .put("label", prep.romLabel).put("checking", prep.romChecking).put("error", prep.romError))
            .put("preparation", prep.preparation?.let { bundleToJson(it) } ?: JSONObject.NULL)
            .put("importing", prep.importing).put("importPhase", prep.importPhase)
            .put("importProgress", prep.importProgress.toDouble()).put("importError", prep.importError)
            .put("imported", prep.imported)
    }

    private fun prepareState(): WebUiResponse = WebUiResponse.json(prepareJson())

    private fun chooseFirmware(id: String): CatalogFirmware {
        val firmware = firmwares().firstOrNull { it.id == id }
        if (firmware == null) throw WebUiError(400, "Unknown firmware $id")
        if (prep.firmwareId != firmware.id) {
            prep.firmwareId = firmware.id
            prep.romPath = ""; prep.romFor = null; prep.romLabel = ""; prep.romError = ""
            app.getSharedPreferences("preparation-imports", Context.MODE_PRIVATE).edit().putString("firmwareId", firmware.id).apply()
        }
        return firmware
    }

    private fun rememberIpsw(path: String, firmwareId: String) {
        prep.ipswPath = path; prep.ipswFor = firmwareId; prep.ipswError = ""
        app.getSharedPreferences("preparation-imports", Context.MODE_PRIVATE).edit()
            .putString("ipsw", path).putString("ipswFor", firmwareId).putString("firmwareId", firmwareId).apply()
    }

    private fun ipswDownload(firmwareId: String): WebUiResponse {
        val firmware = chooseFirmware(firmwareId)
        if (!File(app.applicationInfo.nativeLibraryDir, "libfirmwarekit.so").canExecute())
            throw WebUiError(409, "This build cannot prepare devices; use device files prepared elsewhere")
        if (IpswDownload.running) throw WebUiError(409, "A download is already running")
        IpswDownload.start(app, firmware) { file, error ->
            if (file != null) rememberIpsw(file.absolutePath, firmware.id)
            else { prep.ipswError = error; opError = "The IPSW download failed: $error" }
        }
        return ok("Downloading iOS ${firmware.label}…")
    }

    /** A stock IPSW from the page, checked against the catalog's size and SHA-1 as the app checks its own. */
    private fun ipswUpload(request: WebUiRequest): WebUiResponse {
        val firmware = chooseFirmware(request.param("firmwareId"))
        if (request.contentLength <= 0) throw WebUiError(400, "Send the IPSW as the request body")
        if (firmware.bytes > 0 && request.contentLength != firmware.bytes)
            throw WebUiError(400, "That file is not the iOS ${firmware.label} IPSW")
        val directory = IpswDownload.directory(app).apply { mkdirs() }
        val partial = File(directory, "${firmware.fileName}.partial")
        val digest = MessageDigest.getInstance("SHA-1")
        try {
            request.bodyStream().use { input ->
                partial.outputStream().use { output ->
                    val block = ByteArray(1 shl 16)
                    while (true) {
                        val n = input.read(block)
                        if (n < 0) break
                        output.write(block, 0, n); digest.update(block, 0, n)
                    }
                }
            }
            val sha1 = hex(digest)
            if (firmware.sha1.isNotEmpty() && sha1 != firmware.sha1)
                throw WebUiError(400, "That file is not the iOS ${firmware.label} IPSW")
            val target = File(directory, firmware.fileName)
            if (!partial.renameTo(target)) throw WebUiError(500, "Could not save the IPSW")
            rememberIpsw(target.absolutePath, firmware.id)
        } finally { partial.delete() }
        return ok("iOS ${firmware.label} IPSW ready")
    }

    private fun romTarget(firmwareId: String): Bootrom {
        val firmware = chooseFirmware(firmwareId)
        return Bootrom.forBoard(firmware.board) ?: throw WebUiError(400, "The ${firmware.label} device needs no boot ROM")
    }

    private fun romDownload(firmwareId: String): WebUiResponse {
        val rom = romTarget(firmwareId)
        prep.romChecking = true; prep.romError = ""
        Thread({
            try {
                val target = File(app.filesDir, "prepare-bootrom-${rom.name.lowercase()}.bin")
                rom.fetch(target)
                prep.romPath = target.absolutePath; prep.romFor = rom; prep.romLabel = "Downloaded · ${rom.label}"
            } catch (failure: Exception) { prep.romError = "Could not download the boot file: ${failure.message}" }
            prep.romChecking = false
        }, "webui-bootrom-download").start()
        return ok("Downloading the ${rom.label} boot ROM…")
    }

    private fun romUploadPrepare(request: WebUiRequest): WebUiResponse {
        val rom = romTarget(request.param("firmwareId"))
        if (request.contentLength !in 1..(1L shl 20)) throw WebUiError(400, "That file is not a boot ROM")
        val bytes = request.readBody(1L shl 20)
        if (!rom.matches(bytes)) throw WebUiError(400, "That file is not the ${rom.label} boot ROM")
        val target = File(app.filesDir, "prepare-bootrom-${rom.name.lowercase()}.bin")
        target.writeBytes(bytes)
        prep.romPath = target.absolutePath; prep.romFor = rom; prep.romLabel = "${rom.label}"; prep.romError = ""
        return ok("The ${rom.label} boot ROM is ready")
    }

    private fun startPreparation(firmwareId: String): WebUiResponse {
        if (firmwareId.isNotEmpty()) chooseFirmware(firmwareId)
        if (prep.importing) throw WebUiError(409, "The prepared device is still being added")
        val chosen = firmwares().firstOrNull { it.id == prep.firmwareId }
            ?: throw WebUiError(400, "Choose a device and iOS version first")
        if (!(prep.ipswPath.isNotEmpty() && prep.ipswFor == chosen.id))
            throw WebUiError(409, "Choose the iOS ${chosen.label} IPSW first")
        val bootrom = Bootrom.forBoard(chosen.board)
        if (bootrom != null && !(prep.romPath.isNotEmpty() && prep.romFor == bootrom))
            throw WebUiError(409, "Provide the ${bootrom.label} boot ROM first")
        val active = firmware
        if (active == null) {
            pendingFirmwareStart = true
            firmwareRestarting = true
            bindFirmwareWorker()
            return ok("Starting the preparation worker…")
        }
        val spent = try { active.status().getBoolean("consumed") } catch (_: RemoteException) { true }
        if (spent) { restartFirmwareWorker(); return ok("Restarting the preparation worker…") }
        app.startForegroundService(Intent(app, FirmwareService::class.java))
        val rom = if (bootrom != null) prep.romPath else ""
        prep.preparation = Bundle().apply { putString("state", "starting"); putString("firmwareId", chosen.id) }
        Thread({
            try {
                val tools = GuestTools.firmwareTools(app)
                prep.preparation = active.start(chosen.id, prep.ipswPath, tools.absolutePath, rom)
            } catch (failure: Exception) {
                prep.preparation = Bundle().apply { putString("state", "failed"); putString("error", failure.message ?: "Cannot start preparation") }
            }
        }, "webui-prepare-start").start()
        return ok("Preparing ${chosen.label}…")
    }

    private fun firmwareWorker(): IFirmware? = firmware

    private fun bindFirmwareWorker() {
        synchronized(this) {
            if (firmwareBound) return
            firmwareBound = app.bindService(Intent(app, FirmwareService::class.java), firmwareConnection, Context.BIND_AUTO_CREATE)
        }
    }

    private fun releaseFirmwareWorker() {
        val active = firmware
        synchronized(this) {
            if (!firmwareBound) return
            runCatching { app.unbindService(firmwareConnection) }
            firmwareBound = false
        }
        firmware = null
        firmwareExpectedExit = true
        try { active?.finish() } catch (_: Exception) { }
    }

    private val firmwareConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName, binder: IBinder) {
            firmware = IFirmware.Stub.asInterface(binder)
            firmwareRestarting = false
            if (pendingFirmwareStart) { pendingFirmwareStart = false; startPreparation("") }
        }

        override fun onServiceDisconnected(name: ComponentName) { firmware = null }
    }

    /** The preparation worker runs one preparation per process: end the old one and bind a fresh one. */
    private fun restartFirmwareWorker() {
        val old = firmware
        firmwareRestarting = true
        pendingFirmwareStart = true
        firmwareExpectedExit = true
        synchronized(this) {
            if (firmwareBound) { runCatching { app.unbindService(firmwareConnection) }; firmwareBound = false }
        }
        firmware = null
        val binder = old?.asBinder()
        if (binder == null) { bindFirmwareWorker(); return }
        Thread({
            val died = CountDownLatch(1)
            val recipient = IBinder.DeathRecipient { died.countDown() }
            try {
                if (binder.isBinderAlive) {
                    try { binder.linkToDeath(recipient, 0) } catch (_: DeadObjectException) { died.countDown() }
                    try { old.finish() } catch (_: DeadObjectException) { died.countDown() }
                    if (!binder.isBinderAlive || died.await(5, TimeUnit.SECONDS)) bindFirmwareWorker()
                    else { pendingFirmwareStart = false; firmwareRestarting = false; opError = "The previous preparation worker has not exited; try again in a moment" }
                } else bindFirmwareWorker()
            } catch (failure: Exception) {
                pendingFirmwareStart = false; firmwareRestarting = false
                opError = "Preparation worker restart failed: ${failure.message}"
            } finally { try { binder.unlinkToDeath(recipient, 0) } catch (_: java.util.NoSuchElementException) { } }
        }, "webui-firmware-restart").start()
    }

    /** A finished preparation becomes a device: adopted when this app prepared it, copied otherwise. */
    private fun importPreparedOutput(state: Bundle) {
        if (prep.importing) return
        val destination = state.getString("destination") ?: return
        val firmwareId = state.getString("firmwareId") ?: prep.firmwareId
        prep.importing = true
        prep.imported = false
        prep.importError = ""
        prep.importPhase = "Setting up the iOS system…"
        prep.importProgress = -1f
        prep.importedDestination = destination
        val chosen = firmwares().firstOrNull { it.id == firmwareId }
        val board = chosen?.board ?: firmwareId.substringBefore('-')
        val build = chosen?.build ?: firmwareId.substringAfter('-')
        val bootrom = chosen?.let { Bootrom.forBoard(it.board) }
        val rom = if (bootrom != null && prep.romFor == bootrom) prep.romPath else ""
        val ipsw = prep.ipswPath
        val before = records.mapNotNull { it.getString("id") }.toSet()
        Thread({
            var name = ""
            val engine = worker
            val result: String = try {
                checkNotNull(engine) { "The device worker is unavailable; the prepared device is kept" }
                name = defaultDeviceName(board, build, firmwareTargets(), deviceNames())
                val own = destination.startsWith(app.filesDir.absolutePath) && File(destination, "device.lock.json").isFile
                if (own) engine.adoptPrepared(destination, name, rom) else engine.importPrepared(destination, name, rom)
            } catch (failure: Exception) { failure.message ?: "The device worker exited during import" }
            if (result.isEmpty()) {
                File(destination).walkTopDown().forEach { it.setWritable(true, true) }
                File(destination).deleteRecursively()
                File("$destination.native.log").delete()
                if (ipsw.startsWith(app.filesDir.absolutePath)) File(ipsw).delete()
                try {
                    val created = engine?.records()?.firstOrNull { it.getString("id") !in before }
                    if (created != null && status.getString("state") !in listOf("running", "paused", "starting", "stopping"))
                        engine.select(created.getString("id"))
                } catch (_: RemoteException) { }
                opNotice = "Added $name; stop a running device to switch to it"
            } else {
                prep.importError = result
                opError = "Adding the prepared device failed: $result"
            }
            app.getSharedPreferences("preparation-imports", Context.MODE_PRIVATE).edit()
                .putString("preparedImported", destination).remove("pendingPrepared").remove("ipsw").apply()
            if (result.isEmpty()) {
                prep.imported = true
                if (ipsw.startsWith(app.filesDir.absolutePath)) prep.ipswPath = ""
                if (PreparationNote.read(app)?.optString("destination") == destination) PreparationNote.clear(app)
                releaseFirmwareWorker()
            }
            prep.importing = false
        }, "webui-prepared-import").start()
    }

    private fun retryPreparedImport(): WebUiResponse {
        val state = prep.preparation
        if (prep.importing || state == null || state.getString("state") != "complete")
            throw WebUiError(409, "There is no prepared device to add")
        prep.importedDestination = ""
        prep.imported = false
        prep.importError = ""
        importPreparedOutput(state)
        return ok("Adding the prepared device…")
    }

    // ------------------------------------------------------------------------------------ the store

    /** The device a search or install is checked against: the running guest's own report, else the record. */
    private fun storeTarget(): StoreTarget? {
        val socket = if (status.getString("state") == "running") status.getString("usbMuxSocket") ?: "" else ""
        if (socket.isNotEmpty()) {
            exactTarget?.takeIf { it.first == socket }?.let { return it.second }
            val asked = try { guestApps.target(socket) } catch (_: Exception) { null }
            if (asked != null) { exactTarget = socket to asked; return asked }
        }
        val record = records.firstOrNull { it.getBoolean("selected") } ?: return null
        return firmwareTargets()["${record.getString("board")}-${record.getString("build")}"]
    }

    private fun storeRefresh(): WebUiResponse {
        val socket = if (status.getString("state") == "running") status.getString("usbMuxSocket") ?: "" else ""
        val target = storeTarget()
        val installed = JSONArray()
        if (socket.isNotEmpty() && guestApps.available) {
            val apps = try { guestApps.installedApps(socket) } catch (failure: Exception) {
                throw WebUiError(409, "The guest's app list is unavailable: ${failure.message}")
            }
            for (entry in apps.take(200)) {
                val newest = target?.let { runCatching { LegacyStore.lookup(entry.bundleId, it)?.newest }.getOrNull() }
                installed.put(JSONObject()
                    .put("bundleId", entry.bundleId).put("name", entry.title).put("version", entry.version ?: JSONObject.NULL)
                    .put("newest", newest ?: JSONObject.NULL))
            }
        }
        return WebUiResponse.json(JSONObject()
            .put("target", target?.let { JSONObject().put("productType", it.productType).put("ios", it.ios).put("arch", it.arch) } ?: JSONObject.NULL)
            .put("running", socket.isNotEmpty())
            .put("installed", installed))
    }

    private fun storeSearch(request: WebUiRequest): WebUiResponse {
        val target = storeTarget() ?: throw WebUiError(409, "Select a device first; the store checks every app against it")
        val rows = LegacyStore.search(request.param("q"), target)
        return WebUiResponse.json(JSONObject().put("apps", JSONArray().putAllJson(rows.map { withCompatibility(it) })))
    }

    /** The catalog's row with the one field the server computes and the page cannot: why it will not run. */
    private fun withCompatibility(app: JSONObject): JSONObject {
        LegacyStore.incompatibility(app)?.let { app.put("compatibility", it) }
        return app
    }

    private fun storeListing(fetch: () -> StorePage): WebUiResponse {
        val page = fetch()
        return WebUiResponse.json(JSONObject()
            .put("apps", JSONArray().putAllJson(page.rows.map { withCompatibility(it) }))
            .put("total", page.total).put("next", page.next ?: JSONObject.NULL))
    }

    private fun storeChart(request: WebUiRequest): WebUiResponse {
        val target = storeTarget()
        val genre = request.param("genre").takeIf { it.isNotEmpty() }
        val date = request.param("date").takeIf { it.isNotEmpty() }
        val chart = LegacyStore.chart(request.param("type", "top-free"),
            request.param("device", if (target?.family == "2") "ipad" else "iphone"),
            genre, date)
        val rows = JSONArray()
        for (row in chart.rows) rows.put(JSONObject()
            .put("position", row.position).put("name", row.name).put("developer", row.developer ?: JSONObject.NULL)
            .put("app", row.app?.let { withCompatibility(it) } ?: JSONObject.NULL))
        return WebUiResponse.json(JSONObject().put("chart", JSONObject()
            .put("title", chart.title).put("date", chart.date).put("dates", JSONArray(chart.dates)).put("rows", rows)))
    }

    /** One app's page: what the catalog knows, the versions that run here, and the checked copy. */
    private fun storeApp(body: JSONObject): WebUiResponse {
        val app = body.optJSONObject("app") ?: throw WebUiError(400, "No app was named")
        val target = storeTarget() ?: throw WebUiError(409, "Select a device first")
        val answer = JSONObject().put("app", withCompatibility(app))
        answer.put("info", runCatching { LegacyStore.appInfo(app) }.getOrNull() ?: JSONObject.NULL)
        answer.put("shots", JSONArray().putAllJson(LegacyStore.screenshots(app).map {
            JSONObject().put("url", it.url).put("width", it.width).put("height", it.height) }))
        val copies = try { LegacyStore.versions(app, target) } catch (failure: Exception) { emptyList() }
        answer.put("copies", JSONArray().putAllJson(copies.map {
            JSONObject().put("ipaId", it.ipaId).put("version", it.version)
                .put("size", it.size ?: JSONObject.NULL).put("minimumOS", it.minimumOS ?: JSONObject.NULL) }))
        if (copies.isEmpty()) answer.put("problem", "No version of this app runs on this device.")
        else {
            val chosen = body.optInt("ipaId", 0).takeIf { it > 0 } ?: LegacyStore.preferred(app.optInt("ipa_id"))
            answer.put("selected", copies.firstOrNull { it.ipaId == chosen }?.ipaId ?: copies[0].ipaId)
        }
        return WebUiResponse.json(answer)
    }

    private fun storeInstall(body: JSONObject): WebUiResponse {
        val app = body.optJSONObject("app") ?: throw WebUiError(400, "No app was named")
        val socket = installer.socket ?: throw WebUiError(409, "Start the guest to install apps")
        installer.socket = socket
        val target = storeTarget() ?: throw WebUiError(409, "Select a device first")
        val ipaId = body.optInt("ipaId", 0)
        // The checked copy when a version was chosen; the row itself when one was not (it came from the
        // compatibility-checked search, and the queue re-checks it against the device right before transfer).
        val row = if (ipaId > 0) {
            val candidate = try {
                val found = LegacyStore.compatibleCopy(ipaId, target)
                if (app.optString("bundle_id")?.let { it != found.optString("bundle_id") } == true)
                    throw StoreError("This copy belongs to a different app.")
                LegacyStore.checkCopy(LegacyStore.copyDetails(ipaId), found, target)
                found
            } catch (failure: Exception) { throw WebUiError(409, failure.message ?: "This copy cannot be installed") }
            candidate
        } else app
        val key = "app:" + (row.optString("bundle_id") ?: row.optString("app_store_id") ?: row.optString("app_url")?.trimEnd('/')?.substringAfterLast('/')
            ?: row.optInt("ipa_id").toString())
        if (!installer.queue.enqueue(key, row.optString("name"), InstallRequest.Catalog(row)))
            throw WebUiError(409, "${row.optString("name")} is already in the queue")
        return ok("Queued ${row.optString("name")}")
    }

    /** An IPA from the page: copied into the same private directory a picked file is copied to. */
    private fun ipaUpload(request: WebUiRequest): WebUiResponse {
        if (request.contentLength <= 0) throw WebUiError(400, "Send the IPA as the request body")
        val socket = installer.socket ?: throw WebUiError(409, "Start the guest to install apps")
        installer.socket = socket
        val name = request.param("name").takeIf { it.isNotEmpty() } ?: "Local IPA"
        val directory = File(app.filesDir, "webui-uploads").apply { mkdirs() }
        val file = File(directory, "ipa-${System.nanoTime()}.ipa")
        try {
            request.bodyStream().use { input -> file.outputStream().use { input.copyTo(it) } }
        } catch (failure: Exception) {
            file.delete()
            throw WebUiError(500, "The upload failed: ${failure.message}")
        }
        if (!installer.queue.enqueue("local:${file.absolutePath}", name, InstallRequest.Local(android.net.Uri.fromFile(file))))
            throw WebUiError(409, "That IPA is already in the queue")
        return ok("Queued $name")
    }

    private fun crashReport(): WebUiResponse {
        val socket = installer.socket ?: throw WebUiError(409, "Start the guest to read its crash reports")
        val crash = try { guestApps.lastCrash(socket) } catch (failure: Exception) {
            throw WebUiError(409, "Could not read the crash reports: ${failure.message}")
        }
        return WebUiResponse.json(crash?.let { JSONObject().put("name", it.first).put("text", it.second) }
            ?: JSONObject().put("none", true))
    }

    // ----------------------------------------------------------------------------------- settings

    /** The settings the app's Settings screen shows, from the same SharedPreferences it uses. */
    private fun settingsJson(): WebUiResponse {
        val prefs = app.getSharedPreferences("ui", Context.MODE_PRIVATE)
        val theme = ThemeMode.entries.firstOrNull { it.name == prefs.getString("theme", null) } ?: ThemeMode.SYSTEM
        val scaling = ScreenScaling.entries.firstOrNull { it.name == prefs.getString("scaling", null) } ?: ScreenScaling.SMOOTH
        val overlay = OverlayLevel.entries.firstOrNull { it.name == prefs.getString("overlay", null) } ?: OverlayLevel.OFF
        val corner = OverlayCorner.entries.firstOrNull { it.name == prefs.getString("overlayCorner", null) } ?: OverlayCorner.TOP_LEFT
        val graph = OverlayGraph.entries.firstOrNull { it.name == prefs.getString("overlayGraph", null) } ?: OverlayGraph.FRAME_TIME
        val graphics = GraphicsProvider.entries.firstOrNull { it.name == prefs.getString("graphicsProvider", null) } ?: GraphicsProvider.PLATFORM
        val webUi = WebUiState.port
        return WebUiResponse.json(JSONObject()
            .put("theme", theme.name).put("tilt", prefs.getBoolean("tilt", true))
            .put("volumeKeys", prefs.getBoolean("volumeKeys", false))
            .put("headphones", prefs.getBoolean("headphones", true))
            .put("camera", prefs.getBoolean("camera", false)).put("microphone", prefs.getBoolean("microphone", false))
            .put("haptics", prefs.getBoolean("haptics", true)).put("autoResume", prefs.getBoolean("autoResume", true))
            .put("reduceAppSwitchPauses", prefs.getBoolean("reduceAppSwitchPauses", true))
            .put("graphicsProvider", graphics.name).put("scaling", scaling.name)
            .put("overlay", overlay.name).put("overlayCorner", corner.name).put("overlayGraph", graph.name)
            .put("overlayOpacity", prefs.getFloat("overlayOpacity", 0.55f).toDouble())
            .put("overlayInDeviceView", prefs.getBoolean("overlayInDeviceView", true))
            .put("hitchButton", prefs.getBoolean("hitchButton", false))
            .put("detailedTiming", prefs.getBoolean("detailedTiming", false))
            .put("webUiPort", webUi))
    }

    private fun settingsPost(body: JSONObject): WebUiResponse {
        val prefs = app.getSharedPreferences("ui", Context.MODE_PRIVATE)
        val booleans = mapOf("tilt" to true, "volumeKeys" to false, "headphones" to true, "camera" to false,
            "microphone" to false, "haptics" to true, "autoResume" to true, "reduceAppSwitchPauses" to true,
            "overlayInDeviceView" to true, "hitchButton" to false, "detailedTiming" to false)
        val enums = mapOf("theme" to ThemeMode.entries.map { it.name }, "scaling" to ScreenScaling.entries.map { it.name },
            "overlay" to OverlayLevel.entries.map { it.name }, "overlayCorner" to OverlayCorner.entries.map { it.name },
            "overlayGraph" to OverlayGraph.entries.map { it.name }, "graphicsProvider" to GraphicsProvider.entries.map { it.name })
        val permissions = mapOf("camera" to android.Manifest.permission.CAMERA, "microphone" to android.Manifest.permission.RECORD_AUDIO)
        val editor = prefs.edit()
        for (key in body.keys()) {
            val value = body.get(key) ?: continue
            when {
                key in booleans -> {
                    val on = value.toString().toBooleanStrictOrNull() ?: throw WebUiError(400, "$key is true or false")
                    if (on && key in permissions && app.checkSelfPermission(permissions.getValue(key)) != PackageManager.PERMISSION_GRANTED)
                        throw WebUiError(403, "Grant the ${if (key == "camera") "Camera" else "Microphone"} permission in the app first: turn its switch on once in Un1box's Settings")
                    editor.putBoolean(key, on)
                }
                key in enums -> {
                    val name = value.toString().uppercase()
                    if (name !in enums.getValue(key)) throw WebUiError(400, "$key has no option $name")
                    editor.putString(key, name)
                }
                key == "overlayOpacity" -> editor.putFloat(key, (value.toString().toFloatOrNull() ?: throw WebUiError(400, "$key is a number"))
                    .coerceIn(0f, 0.9f))
                else -> throw WebUiError(400, "No setting named $key")
            }
        }
        editor.apply()
        // What the running worker reads, the same two the app's applySettings pushes.
        val worker = worker
        if (worker != null) {
            val scaling = ScreenScaling.entries.firstOrNull { it.name == prefs.getString("scaling", null) }?.native ?: 0
            val detailed = prefs.getBoolean("detailedTiming", false)
            Thread({ try { worker.setPresentScale(scaling); worker.setDetailedTiming(detailed) } catch (_: RemoteException) { } },
                "webui-apply-settings").start()
        }
        return settingsJson()
    }
}

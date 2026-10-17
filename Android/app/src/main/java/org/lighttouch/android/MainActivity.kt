package org.lighttouch.android

import android.content.*
import android.hardware.*
import android.net.Uri
import android.provider.MediaStore
import org.json.JSONObject
import android.os.*
import android.content.pm.ActivityInfo
import android.view.WindowInsets as PlatformInsets
import android.view.WindowInsetsController
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.*
import androidx.compose.ui.unit.dp
import androidx.compose.ui.text.style.TextOverflow
import java.util.Locale
import androidx.documentfile.provider.DocumentFile
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.Executors

@OptIn(androidx.compose.ui.ExperimentalComposeUiApi::class)
class MainActivity : ComponentActivity(), SensorEventListener {
    private var engine by mutableStateOf<IEngine?>(null)
    private var status by mutableStateOf("Stopped")
    private var detail by mutableStateOf("")
    private var displayAsleep by mutableStateOf(false)
    private var error by mutableStateOf("")
    private val workerEvidenceDelivery = SessionDeliveryGate()
    private var workerPid by mutableIntStateOf(0)
    private var sessionOwned by mutableStateOf(false)
    private data class GuestStartRequest(val recordId: String, val coldRecovery: Boolean)
    private var pendingGuestRequest: GuestStartRequest? = null
    private var pendingGuestStart = false
    private var guestStartRequested = false
    private var engineExpectedExit = false
    private var engineRestarting = false
    private var busy by mutableStateOf(false)
    private var captureNotice by mutableStateOf("")
    private var licensesOpen by mutableStateOf(false)
    private var performanceOpen by mutableStateOf(false)
    private var performanceData by mutableStateOf<Bundle?>(null)
    private var performanceUnavailable by mutableStateOf(false)
    // Observation identity only; this grants no input/display/control authority.
    private var performanceEpoch = SystemClock.elapsedRealtimeNanos()
    private var performanceInFlight = false
    private var performanceNowMs by mutableLongStateOf(0L)
    // Guest frames per 250 ms over the last 60 s, from the engine process; null when not running.
    private var guestFrameBuckets by mutableStateOf<IntArray?>(null)
    private var muxSocket by mutableStateOf("")
    private var appServicesError by mutableStateOf("")
    private var storeOpen by mutableStateOf(false)
    private val store by lazy { StoreModel(applicationContext) { muxSocket.ifEmpty { null } } }
    private val localIpa = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> if (uri != null) store.installLocal(uri) }
    // board-build -> (product type, iOS version), from the bundled firmware catalog.
    @Volatile private var firmwareTargets: Map<String, StoreTarget> = emptyMap()
    private var fullscreen by mutableStateOf(false)
    private var tiltEnabled by mutableStateOf(true)
    // Hardware buttons are Binder calls; one thread keeps down/up ordered and off the UI thread.
    private val buttonCalls = Executors.newSingleThreadExecutor { Thread(it, "guest-buttons") }
    private var devices by mutableStateOf<List<Bundle>>(emptyList())
    private var selected by mutableStateOf<Bundle?>(null)
    private var deviceMenu by mutableStateOf(false)
    private var firmware: IFirmware? = null
    private var firmwareBound = false
    private var firmwareRestarting by mutableStateOf(false)
    private var pendingFirmwareStart = false
    private var firmwareExpectedExit = false
    private var retainedPreparedOutput = ""
    private var preparedImportInFlight = false
    private var preparationOpen by mutableStateOf(false)
    private var firmwareIds by mutableStateOf<List<Pair<String, Boolean>>>(emptyList())
    private var firmwareId by mutableStateOf("")
    private var firmwareMenu by mutableStateOf(false)
    private var preparation by mutableStateOf<Bundle?>(null)
    private var preparedImported = ""
    private var preparedImportError by mutableStateOf("")
    private var ipswSelected by mutableStateOf(false)
    private var toolsSelected by mutableStateOf(false)
    private var toolsPath = ""
    private var ipswPath = ""
    private val firmwareConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName, binder: IBinder) {
            val worker = IFirmware.Stub.asInterface(binder)
            firmware = worker
            firmwareExpectedExit = false
            binder.linkToDeath({ handler.post {
                if (firmware?.asBinder() == binder) {
                    firmware = null
                    if (!firmwareRestarting && !firmwareExpectedExit) preparation = Bundle().apply { putString("state", "failed"); putString("error", "Firmware worker exited; completed output is retained and interrupted staging is not admitted.") }
                }
            } }, 0)
            val workerState = worker.status()
            preparation = if (workerState.getString("state") == "idle" && retainedPreparedOutput.isNotEmpty()) retainedPreparation() else workerState
            if (pendingFirmwareStart) {
                pendingFirmwareStart = false
                firmwareRestarting = false
                if (preparation?.getBoolean("consumed") == true) restartFirmwareForPreparation() else beginPreparation()
            }
            Thread({
                try {
                    val entries = JSONObject(assets.open("firmware-catalog.json").bufferedReader().use { it.readText() }).getJSONArray("entries")
                    val choices = (0 until entries.length()).map { entries.getJSONObject(it).getString("id") }.map { it to worker.supports(it) }
                    handler.post { if (firmware?.asBinder() != binder) return@post; firmwareIds = choices; if (firmwareId.isEmpty()) firmwareId = choices.firstOrNull { it.second }?.first ?: "" }
                } catch (e: Exception) { handler.post { if (firmware?.asBinder() == binder) error = e.message ?: "Cannot load firmware catalog" } }
            }, "firmware-catalog").start()
        }
        override fun onServiceDisconnected(name: ComponentName) { firmware = null; if (!firmwareRestarting && !firmwareExpectedExit) preparation = Bundle().apply { putString("state", "failed"); putString("error", "Firmware worker exited; completed output is retained and interrupted staging is not admitted.") } }
    }
    private val stockIpsw = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> if (uri != null) importStockIpsw(uri) }
    private val trustedTools = registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri -> if (uri != null) importTrustedTools(uri) }
    private var active = false
    private var displayOwnerTicket = 0L
    private var displayResumed = false
    private fun ownsDisplay() = active && DisplayOwners.active.owns(this, displayOwnerTicket)
    private fun mayClaimDisplay() = active && displayResumed && DisplayOwners.active.canClaim(this, displayOwnerTicket)
    private var bound = false
    private var displayView: GuestDisplay? = null
    private val handler = Handler(Looper.getMainLooper())
    private val sensors by lazy { getSystemService(SensorManager::class.java) }
    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName, binder: IBinder) {
            workerEvidenceDelivery.advance()
            engine = IEngine.Stub.asInterface(binder)
            resetPerformanceObservation()
            refreshDevices()
            binder.linkToDeath({ handler.post { if (engine?.asBinder() == binder) workerExited() } }, 0)
            if (pendingGuestStart) {
                val worker = requireNotNull(engine)
                Thread({
                    try { val state = worker.status(); handler.post {
                        if (engine?.asBinder() != binder) return@post
                        sessionOwned = state.getBoolean("sessionStarted"); status = state.getString("state") ?: "stopped"
                        pendingGuestStart = false; engineRestarting = false
                        pendingGuestRequest?.let { startGuest(retainedRequest = it) }
                            ?: run { error = "Guest start request was lost; select Start or Recover retained state again."; status = "failed" }
                    } } catch (failure: RemoteException) { handler.post { pendingGuestStart = false; workerExited() } }
                }, "guest-reconnect").start()
            }
            displayView?.attachActiveSurface()
        }
        override fun onServiceDisconnected(name: ComponentName) { workerExited() }
    }
    private var pollInFlight = false
    private val poll = object : Runnable {
        override fun run() {
            if (!active || pollInFlight) return
            pollInFlight = true
            val observedEngine = engine
            val observedFirmware = if (preparationOpen && !firmwareRestarting) firmware else null
            Thread({
                var engineState: Bundle? = null
                var firmwareState: Bundle? = null
                var engineFailure: String? = null
                var firmwareFailure: String? = null
                try { engineState = observedEngine?.status() } catch (e: RemoteException) { engineFailure = e.message ?: "Worker exited" }
                try { firmwareState = observedFirmware?.status() } catch (e: RemoteException) { firmwareFailure = e.message ?: "Firmware worker exited" }
                handler.post {
                    pollInFlight = false
                    if (!active) return@post
                    if (engine?.asBinder() == observedEngine?.asBinder()) {
                        engineState?.let { b ->
                            status = b.getString("state") ?: "Unknown"
                            detail = b.getString("detail") ?: ""
                            displayAsleep = b.getBundle("observations")?.let {
                                it.getBoolean("runtimeDiagnosticsAvailable") && it.getLong("engineReady") != 0L && it.getLong("displaySleeping") != 0L
                            } ?: false
                            val reportedError = b.getString("error") ?: ""
                            if (reportedError.isNotEmpty() || status == "running") error = reportedError
                            workerPid = b.getInt("pid")
                            guestFrameBuckets = if (status == "running") b.getIntArray("guestFrameBuckets") else null
                            sessionOwned = b.getBoolean("sessionStarted")
                            val socket = if (status == "running") b.getString("usbMuxSocket") ?: "" else ""
                            if (socket != muxSocket) { muxSocket = socket; if (socket.isNotEmpty() && storeOpen) store.refreshGuest() }
                            appServicesError = b.getString("appServicesError") ?: ""
                            displayView?.keepScreenOn = status in listOf("starting", "running", "stopping")
                            if (status == "running") startSensors() else stopSensors()
                        }
                        engineFailure?.let { workerExited(); if (!engineExpectedExit) error = "Guest worker unavailable: $it. Writable state retained; Start creates a fresh worker." }
                    }
                    if (firmware?.asBinder() == observedFirmware?.asBinder() && !firmwareRestarting) {
                        firmwareState?.let { pollPreparation(it) }
                        firmwareFailure?.let { error = "Firmware worker unavailable: $it" }
                    }
                    handler.postDelayed(this, 500)
                }
            }, "worker-status").start()
        }
    }
    // Independent from Native.status: even a blocked status poll cannot queue PSS
    // reads or hold up this cached request. At most one UI Binder read is in flight.
    private val performancePoll = object : Runnable {
        override fun run() {
            if (!active || !performanceOpen) return
            performanceNowMs = SystemClock.elapsedRealtime()
            val worker = engine
            if (worker == null) { performanceData = null; performanceUnavailable = true }
            val epoch = performanceEpoch
            if (!performanceInFlight && worker != null) {
                performanceInFlight = true
                Thread({
                    val sample = try { worker.performance(epoch) } catch (_: RemoteException) { null }
                    handler.post {
                        performanceInFlight = false
                        if (active && performanceOpen && performanceEpoch == epoch && engine?.asBinder() == worker.asBinder()) {
                            performanceData = sample
                            performanceUnavailable = sample == null
                            performanceNowMs = SystemClock.elapsedRealtime()
                        }
                    }
                }, "performance-cache-read").start()
            }
            handler.postDelayed(this, 1_000)
        }
    }
    private fun resetPerformanceObservation() {
        performanceEpoch++
        performanceData = null
        performanceUnavailable = false
        handler.removeCallbacks(performancePoll)
        if (active && performanceOpen) handler.post(performancePoll)
    }
    private fun togglePerformance() {
        performanceOpen = !performanceOpen
        resetPerformanceObservation()
    }
    private fun performanceAge(sample: Bundle?, key: String): String {
        if (sample == null || !sample.containsKey(key)) return ""
        val stamp = sample.getLong(key)
        if (performanceNowMs < stamp) return " · age unavailable"
        return " · ${(performanceNowMs - stamp) / 1_000}s ago"
    }
    private fun performanceNumber(value: Double) = String.format(Locale.ROOT, "%.1f", value)

    private val importTree = registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) importDirectory(uri)
    }
    private val importArchive = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) importBaseArchive(uri)
    }
    private val importFile = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) copyAsset(uri)
    }
    private var assetName = "bootrom.bin"
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Retain the previous own-worker crash even when it happened before this
        // activity/process was running. Never request another application's trace.
        Thread({ WorkerExitEvidence.capture(applicationContext, 0) }, "previous-worker-exit").start()
        val imports = getSharedPreferences("preparation-imports", MODE_PRIVATE)
        ipswPath = imports.getString("ipsw", "") ?: ""; toolsPath = imports.getString("tools", "") ?: ""
        ipswSelected = File(ipswPath).isFile; toolsSelected = File(toolsPath).isDirectory
        firmwareId = imports.getString("firmwareId", "") ?: ""
        preparedImported = imports.getString("preparedImported", "") ?: ""
        retainedPreparedOutput = imports.getString("pendingPrepared", "") ?: ""
        if (retainedPreparedOutput.isNotEmpty()) {
            preparation = retainedPreparation(); preparedImported = retainedPreparedOutput
            preparedImportError = "Completed preparation is retained; retry importing it as a device."
        }
        val ui = getSharedPreferences("ui", MODE_PRIVATE)
        tiltEnabled = ui.getBoolean("tilt", true)
        fullscreen = ui.getBoolean("fullscreen", false)
        Thread({
            try {
                val entries = JSONObject(assets.open("firmware-catalog.json").bufferedReader().use { it.readText() }).getJSONArray("entries")
                firmwareTargets = (0 until entries.length()).map { entries.getJSONObject(it) }
                    .filter { it.has("product_type") && it.has("version") }
                    .associate { "${it.getString("board")}-${it.getString("build")}" to StoreTarget.of(it.getString("product_type"), it.getString("version")) }
                handler.post { if (storeOpen) store.useRecord(recordTarget()) }
            } catch (_: Exception) { /* The Store falls back to the running guest's own report. */ }
        }, "store-targets").start()
        bindWorker()
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(primary = Color(0xFF71DCC8))) {
                // One SurfaceView moves between the windowed and device layouts, keeping its display lease.
                val guestSurface = remember { movableContentOf { modifier: Modifier ->
                    WorkerGuestSurface(worker = engine, currentWorker = { engine },
                        mayClaim = { view -> mayClaimDisplay() && displayView === view },
                        ownerTicket = { displayOwnerTicket }, onError = { error = it },
                        onCreated = { displayView = it },
                        onReleased = { view -> if (displayView === view) displayView = null },
                        keepScreenOn = active && status in listOf("starting", "running", "stopping"),
                        modifier = modifier.background(Color.Black).testTag("guest_display"))
                } }
                if (fullscreen) {
                    BackHandler { showDeviceView(false) }
                    Box(Modifier.fillMaxSize().background(Color.Black).semantics { testTagsAsResourceId = true }) {
                        DeviceFrame(DeviceSkin.forBoard(selected?.getString("board")), enabled = status == "running",
                            button = ::sendButton, screen = { guestSurface(it) }, modifier = Modifier.fillMaxSize().padding(8.dp))
                        TextButton(onClick = { showDeviceView(false) }, modifier = Modifier.align(Alignment.TopEnd).testTag("exit_fullscreen")) { Text("Exit", color = Color(0xFF8E8E93)) }
                        if (status != "running") Text(status, Modifier.align(Alignment.BottomCenter).padding(16.dp), color = Color(0xFF8E8E93))
                    }
                } else
                Surface(Modifier.fillMaxSize()) {
                    Column(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing).padding(16.dp).semantics { testTagsAsResourceId = true }) {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text("LightTouch", style = MaterialTheme.typography.headlineSmall, modifier = Modifier.weight(1f), maxLines = 1)
                            TextButton(onClick = { openStore() }, modifier = Modifier.testTag("open_store"), contentPadding = PaddingValues(horizontal = 6.dp)) { Text("Store") }
                            TextButton(onClick = { togglePerformance() }, modifier = Modifier.testTag("toggle_performance").semantics { stateDescription = if (performanceOpen) "Expanded" else "Collapsed" }, contentPadding = PaddingValues(horizontal = 6.dp)) { Text("Performance") }
                            TextButton(onClick = { licensesOpen = true }, modifier = Modifier.testTag("open_licenses"), contentPadding = PaddingValues(horizontal = 6.dp)) { Text("Licenses") }
                        }
                        if (performanceOpen) {
                            val sample = performanceData?.takeIf { engine != null && workerPid > 0 && it.getInt("pid") == workerPid }
                            val unavailable = performanceUnavailable || engine == null
                            val cpu = if (sample?.containsKey("workerCpuPercentOfOneCore") == true)
                                performanceNumber(sample.getDouble("workerCpuPercentOfOneCore")) + "% of one core"
                                else if (unavailable) "unavailable" else sample?.getString("cpuState") ?: "warming up"
                            val cpuSeconds = PerformanceCpuReading.cumulativeSeconds(sample?.getInt("pid") ?: 0, workerPid,
                                sample?.takeIf { it.containsKey("processCpuMs") }?.getLong("processCpuMs"))
                            val cpuTotal = cpuSeconds?.let { performanceNumber(it) + "s total" }
                                ?: if (!unavailable && sample?.containsKey("processCpuMs") != true) "warming up" else "unavailable"
                            val pss = if (sample?.containsKey("workerPssKiB") == true)
                                performanceNumber(sample.getLong("workerPssKiB") / 1024.0) + " MiB PSS" else if (sample == null && !unavailable) "warming up" else "unavailable"
                            val ram = if (sample?.containsKey("deviceTotalBytes") == true)
                                performanceNumber(sample.getLong("deviceAvailableBytes") / 1048576.0) + " / " + performanceNumber(sample.getLong("deviceTotalBytes") / 1048576.0) + " MiB available/total" else if (sample == null && !unavailable) "warming up" else "unavailable"
                            // Constant row geometry: changing samples do not resize the guest.
                            listOf(
                                "QEMU worker CPU: $cpu" + performanceAge(sample, "cpuSampleElapsedMs"),
                                "QEMU worker CPU time: $cpuTotal" + performanceAge(sample, "cpuSampleElapsedMs"),
                                "Emulator memory: $pss" + performanceAge(sample, "pssSampleElapsedMs"),
                                "Android RAM: $ram" + performanceAge(sample, "deviceMemorySampleElapsedMs"),
                                "Android whole-device CPU: unavailable",
                                "iOS guest CPU / RAM: unavailable",
                                "Worker PID: " + (sample?.getInt("pid")?.toString() ?: "unavailable") + " · sampled every 2s while open"
                            ).forEach { line -> Text(line, modifier = Modifier.fillMaxWidth().height(22.dp), style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis) }
                        }
                        Box {
                            OutlinedButton(onClick = { refreshDevices(); deviceMenu = true }, enabled = !busy && status !in listOf("running", "paused", "starting", "stopping"), modifier = Modifier.testTag("device_selector")) {
                                Text(selected?.let { "${it.getString("name")} · ${it.getString("board")} ${it.getString("build")}" } ?: "Select or import device")
                            }
                            DropdownMenu(expanded = deviceMenu, onDismissRequest = { deviceMenu = false }) {
                                for (device in devices) DropdownMenuItem(text = { Text("${device.getString("name")} · ${device.getString("board")} ${device.getString("build")}") }, onClick = {
                                    busy = true; deviceMenu = false
                                    val worker = engine
                                    Thread({
                                        val result = try { worker?.select(device.getString("id")) ?: "Worker unavailable" }
                                            catch (e: RemoteException) { e.message ?: "Device selection failed" }
                                        handler.post { error = result; busy = false; refreshDevices() }
                                    }, "device-selection").start()
                                })
                            }
                        }
                        selected?.let { record ->
                            val sensors = listOfNotNull("accelerometer".takeIf { record.getBoolean("acceleration") }, "gyroscope".takeIf { record.getBoolean("gyro") })
                            Text("${record.getString("contacts")}-finger touch" + if (sensors.isEmpty()) "" else "; " + sensors.joinToString(" and "), style = MaterialTheme.typography.bodySmall)
                        }
                        Text(status, Modifier.testTag("session_status"), style = MaterialTheme.typography.titleMedium)
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text(detail, Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
                            // Fixed size whether or not it has data, so starting a guest does not move the layout.
                            FpsGraph(guestFrameBuckets, Modifier.size(144.dp, 40.dp).testTag("fps_graph"))
                        }
                        if (captureNotice.isNotEmpty()) Text(captureNotice, Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                        if (error.isNotEmpty()) Text(error, Modifier.testTag("session_error").semantics { liveRegion = LiveRegionMode.Polite }, color = MaterialTheme.colorScheme.error)
                        if (status !in listOf("running", "paused", "starting", "stopping")) {
                        TextButton(onClick = { preparationOpen = true; bindFirmware() }, enabled = !busy && status !in listOf("running", "paused", "starting", "stopping"), modifier = Modifier.testTag("prepare_firmware")) { Text("Prepare from stock IPSW") }
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = { importArchive.launch(arrayOf("application/octet-stream", "*/*")) }, enabled = !busy && status !in listOf("running", "paused", "starting", "stopping"), contentPadding = PaddingValues(horizontal = 12.dp), modifier = Modifier.testTag("import_base")) { Text("Import base") }
                            Button(onClick = { assetName = "bootrom.bin"; importFile.launch(arrayOf("application/octet-stream", "*/*")) }, enabled = !busy, contentPadding = PaddingValues(horizontal = 12.dp), modifier = Modifier.semantics { contentDescription = "Import optional ROM" }) { Text("ROM") }
                            Button(onClick = { assetName = "guest.itpack"; importFile.launch(arrayOf("application/octet-stream", "*/*")) }, enabled = !busy, contentPadding = PaddingValues(horizontal = 12.dp)) { Text("Guest tools") }
                        }
                        TextButton(onClick = { importTree.launch(null) }, enabled = !busy && status !in listOf("running", "paused", "starting", "stopping")) { Text("Import prepared folder instead") }
                        }
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = { startGuest() }, enabled = !busy && selected != null && selected?.getBoolean("dirty") != true && status !in listOf("running", "starting", "stopping", "paused"), modifier = Modifier.testTag("start_guest")) { Text("Start") }
                            Button(onClick = { stopGuest() }, enabled = sessionOwned && status in listOf("starting", "running", "paused", "failed") && !store.installing, modifier = Modifier.testTag("stop_guest")) { Text("Stop") }
                            Button(onClick = {
                                val pausing = status != "paused"
                                if (pausing) displayView?.cancelOwnedGesture()
                                engine?.pause(pausing)
                            }, enabled = status in listOf("running", "paused")) { Text(if (status == "paused") "Resume" else "Pause") }
                        }
                        if (selected != null && status !in listOf("running", "paused", "starting", "stopping")) {
                            TextButton(onClick = { startGuest(coldRecovery = true) }, enabled = !busy,
                                modifier = Modifier.testTag("recover_guest")) { Text("Recover retained state") }
                            if (selected?.getBoolean("dirty") == true) Text(
                                "The previous shutdown was unconfirmed. Recovery boots retained writable data and keeps private diagnostics.",
                                style = MaterialTheme.typography.bodySmall)
                            else if (status == "failed") Text(
                                "Recovery boots retained writable data and keeps private diagnostics; it does not confirm a previous shutdown.",
                                style = MaterialTheme.typography.bodySmall)
                        }
                        // The asleep notice overlays the (black) guest view: inserting it above shifted the
                        // view on every sleep and wake, under the user's finger.
                        Box(Modifier.weight(1f).fillMaxWidth()) {
                            guestSurface(Modifier.fillMaxSize())
                            if (displayAsleep && status == "running") Text("Guest screen asleep; use Power to wake.",
                                modifier = Modifier.align(Alignment.Center).padding(16.dp).testTag("guest_screen_asleep"), color = Color(0xFF8E8E93))
                        }
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = { pulse(0) }, enabled = status == "running", modifier = Modifier.testTag("home_button")) { Text("Home") }
                            Button(onClick = { pulse(1) }, enabled = status == "running", modifier = Modifier.testTag("power_button")) { Text("Power") }
                            Button(onClick = { capture() }, enabled = status in listOf("running", "paused"), modifier = Modifier.testTag("screenshot_button")) { Text("Screenshot") }
                        }
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Switch(tiltEnabled, { setTilt(it) }, Modifier.testTag("tilt_toggle"))
                            Column(Modifier.weight(1f).padding(start = 8.dp)) {
                                Text("Tilt controls", style = MaterialTheme.typography.bodyMedium, maxLines = 1)
                                if (tiltReadout.isNotEmpty()) Text(tiltReadout, style = MaterialTheme.typography.bodySmall, maxLines = 1, modifier = Modifier.testTag("tilt_readout"))
                            }
                            FilledTonalButton(onClick = { showDeviceView(true) }, modifier = Modifier.testTag("fullscreen_button")) { Text("Full screen", maxLines = 1, softWrap = false) }
                        }
                        Text("Backgrounding pauses the guest. Reopen and tap Resume.", style = MaterialTheme.typography.bodySmall)
                    }
                }
                // Inside the theme: dialogs composed outside it rendered in the default light scheme.
                if (storeOpen && !fullscreen) StoreScreen(store, running = status == "running" && muxSocket.isNotEmpty(), onPickLocal = { localIpa.launch(arrayOf("application/octet-stream", "*/*")) }) { storeOpen = false }
                if (preparationOpen) PreparationDialog()
                if (licensesOpen) LicenseDialog { licensesOpen = false }
            }
        }
    }
    /**
     * Guest fps over the last 60 s on a fixed 0-60 scale: each point is the rate over 500 ms, so a dip
     * shows as a dip instead of being averaged into the second around it. The current rate (last 1 s)
     * floats at the head of the trace; the corner has the average and the lowest point over the last 10 s.
     */
    @Composable
    private fun FpsGraph(buckets: IntArray?, modifier: Modifier) {
        val trace = MaterialTheme.colorScheme.primary
        Canvas(modifier) {
            val first = buckets?.indexOfFirst { it > 0 } ?: -1
            if (buckets == null || first < 0) return@Canvas
            drawRect(Color(0xFF1C1C1E))
            val top = 2f
            fun y(fps: Float) = size.height - fps.coerceIn(0f, 60f) / 60f * (size.height - top)
            drawLine(Color(0xFF48484A), Offset(0f, y(30f)), Offset(size.width, y(30f)), 1f)
            drawLine(Color(0xFF48484A), Offset(0f, y(60f)), Offset(size.width, y(60f)), 1f)
            val step = size.width / (buckets.size - 1)
            val start = maxOf(first, 1)
            val points = (start until buckets.size).map { i -> i * step to (buckets[i - 1] + buckets[i]) * 2f }
            if (points.size >= 2) {
                val line = Path(); val fill = Path()
                points.forEachIndexed { k, (x, fps) -> if (k == 0) line.moveTo(x, y(fps)) else line.lineTo(x, y(fps)) }
                fill.addPath(line); fill.lineTo(points.last().first, size.height); fill.lineTo(points.first().first, size.height); fill.close()
                drawPath(fill, trace.copy(alpha = 0.25f))
                drawPath(line, trace, style = Stroke(1.5f))
            }
            val now = buckets.takeLast(4).sum().toFloat()
            val recent = maxOf(first, buckets.size - 40)
            val avg = buckets.drop(recent).sum() * 4f / (buckets.size - recent)
            val low = points.filter { it.first >= recent * step }.minOfOrNull { it.second } ?: now
            drawIntoCanvas { canvas ->
                val paint = android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG).apply {
                    textSize = 11.dp.toPx(); color = trace.toArgb(); textAlign = android.graphics.Paint.Align.RIGHT
                    typeface = android.graphics.Typeface.DEFAULT_BOLD
                    setShadowLayer(3f, 0f, 0f, android.graphics.Color.BLACK)
                }
                val label = String.format(Locale.ROOT, "%.0f", now)
                val headY = (y(now) + paint.textSize / 2).coerceIn(paint.textSize, size.height - 1f)
                canvas.nativeCanvas.drawText(label, size.width - 3f, headY, paint)
                paint.apply { textSize = 9.dp.toPx(); color = 0xFFAEAEB2.toInt(); textAlign = android.graphics.Paint.Align.LEFT; typeface = android.graphics.Typeface.DEFAULT }
                canvas.nativeCanvas.drawText(String.format(Locale.ROOT, "10 s avg %.0f  low %.0f", avg, low), 3f, paint.textSize + 1f, paint)
            }
        }
    }
    @Composable
    private fun PreparationDialog() {
        val inputsLocked = busy || preparationActive()
        AlertDialog(onDismissRequest = { if (!inputsLocked) closePreparation() },
            title = { Text("Prepare firmware") },
            text = {
                Column {
                    Text("Choose a legitimate stock IPSW and the verified guest tools folder. Preparation runs locally without root.")
                    Box {
                        OutlinedButton(onClick = { firmwareMenu = true }, enabled = !inputsLocked) { Text(firmwareId.ifEmpty { "Choose firmware" }) }
                        DropdownMenu(expanded = firmwareMenu, onDismissRequest = { firmwareMenu = false }) {
                            for ((id, supported) in firmwareIds) DropdownMenuItem(enabled = !inputsLocked, text = { Text(if (supported) id else "$id · unsupported route") }, onClick = { firmwareId = id; getSharedPreferences("preparation-imports", MODE_PRIVATE).edit().putString("firmwareId", id).apply(); firmwareMenu = false })
                        }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = { stockIpsw.launch(arrayOf("application/octet-stream", "*/*")) }, enabled = !inputsLocked) { Text("Stock IPSW") }
                        Button(onClick = { trustedTools.launch(null) }, enabled = !inputsLocked) { Text("Verified tools") }
                    }
                    Text(if (ipswSelected) "Stock IPSW selected" else "Select the stock IPSW")
                    Text(if (toolsSelected) "Verified tools selected" else "Select the verified handoff tools folder")
                    preparation?.let { state ->
                        Text("${state.getString("state")} · ${state.getString("stage")}", Modifier.testTag("preparation_status"))
                        if (state.getLong("total") > 0) LinearProgressIndicator(progress = { (state.getLong("done").toFloat() / state.getLong("total")).coerceIn(0f, 1f) })
                        state.getString("error")?.takeIf { it.isNotEmpty() }?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                        if (preparedImportError.isNotEmpty()) Text("Prepared output retained: $preparedImportError", color = MaterialTheme.colorScheme.error)
                    }
                }
            },
            confirmButton = {
                if (preparation?.getString("state") == "complete" && preparedImportError.isNotEmpty()) TextButton(onClick = { preparation?.let { importPreparedOutput(it) } }, enabled = !busy) { Text("Retry import") }
                else if (preparationActive()) TextButton(onClick = { firmware?.cancel(); preparation = preparation?.apply { putString("state", "cancelling") } }, enabled = !firmwareRestarting && preparation?.getString("state") != "cancelling", modifier = Modifier.testTag("cancel_preparation")) { Text("Cancel") }
                else TextButton(onClick = { beginPreparation() }, enabled = !inputsLocked && ipswSelected && toolsSelected && firmwareIds.any { it.first == firmwareId && it.second }, modifier = Modifier.testTag("start_preparation")) { Text("Prepare") }
            },
            dismissButton = { TextButton(onClick = { closePreparation() }, enabled = !inputsLocked) { Text("Close") } })
    }
    private fun retainedPreparation() = Bundle().apply {
        putString("state", "complete"); putString("stage", "Prepared output retained")
        putString("destination", retainedPreparedOutput); putString("firmwareId", firmwareId)
        putBoolean("consumed", false)
    }
    private fun preparationActive() = firmwareRestarting || preparation?.getString("state") in listOf("starting", "preparing", "cancelling")
    private fun closePreparation() {
        if (busy || preparationActive()) return
        preparationOpen = false
        firmwareExpectedExit = true
        try { firmware?.finish() } catch (e: RemoteException) { error = e.message ?: "Firmware worker unavailable" }
    }
    private fun bindFirmware() {
        if (firmwareBound) { unbindService(firmwareConnection); firmwareBound = false }
        firmwareBound = bindService(Intent(this, FirmwareService::class.java), firmwareConnection, BIND_AUTO_CREATE)
    }
    private fun beginPreparation() {
        if (preparationActive() || busy) return
        if (preparation?.getBoolean("consumed") == true) { restartFirmwareForPreparation(); return }
        val worker = firmware ?: run { pendingFirmwareStart = true; firmwareRestarting = true; bindFirmware(); return }
        val selectedId = firmwareId; val selectedIpsw = ipswPath; val selectedTools = toolsPath
        preparedImportError = ""
        preparation = Bundle().apply { putString("state", "starting") }
        startForegroundService(Intent(this, FirmwareService::class.java))
        Thread({ try { val state = worker.start(selectedId, selectedIpsw, selectedTools); handler.post { preparation = state } }
            catch (e: Exception) { handler.post { error = e.message ?: "Cannot start preparation"; preparation = Bundle().apply { putString("state", "failed"); putString("error", error); putBoolean("consumed", true) } } } }, "prepare-start").start()
    }
    private fun restartFirmwareForPreparation() {
        if (firmwareRestarting) return
        val old = firmware ?: run { pendingFirmwareStart = true; firmwareRestarting = true; bindFirmware(); return }
        val oldBinder = old.asBinder()
        firmwareRestarting = true
        firmwareExpectedExit = true
        pendingFirmwareStart = true
        if (firmwareBound) { unbindService(firmwareConnection); firmwareBound = false }
        Thread({
            val died = CountDownLatch(1)
            val recipient = IBinder.DeathRecipient { died.countDown() }
            try {
                if (oldBinder.isBinderAlive) {
                    try { oldBinder.linkToDeath(recipient, 0) } catch (_: DeadObjectException) { died.countDown() }
                    try { old.finish() } catch (_: DeadObjectException) { died.countDown() }
                    check(!oldBinder.isBinderAlive || died.await(5, TimeUnit.SECONDS)) { "Previous firmware worker has not exited. Retry after it stops." }
                }
                handler.post { firmware = null; preparation = null; bindFirmware() }
            } catch (e: Exception) { handler.post { pendingFirmwareStart = false; firmwareRestarting = false; error = e.message ?: "Firmware worker restart failed" } }
            finally { try { oldBinder.unlinkToDeath(recipient, 0) } catch (_: java.util.NoSuchElementException) { } }
        }, "firmware-worker-restart").start()
    }
    private fun pollPreparation(state: Bundle) {
        if (firmwareRestarting) return
        if (state.getString("state") == "idle" && retainedPreparedOutput.isNotEmpty()) return
        preparation = state
        if (state.getString("state") == "complete" && state.getString("destination") != preparedImported) {
            retainedPreparedOutput = state.getString("destination") ?: ""
            getSharedPreferences("preparation-imports", MODE_PRIVATE).edit().putString("pendingPrepared", retainedPreparedOutput).apply()
        }
        state.getString("firmwareId")?.takeIf { it.isNotEmpty() }?.let { firmwareId = it }
        if (!preparedImportInFlight && state.getString("state") == "complete" && state.getString("destination") != preparedImported) importPreparedOutput(state)
    }
    private fun importPreparedOutput(state: Bundle) {
        if (preparedImportInFlight) return
        val destination = requireNotNull(state.getString("destination")); preparedImported = destination; busy = true; preparedImportInFlight = true
        Thread({
            val result = try { engine?.importPrepared(destination, "$firmwareId prepared") ?: "Device worker unavailable" }
                catch (e: RemoteException) { e.message ?: "Device worker exited during import" }
            handler.post {
                busy = false; preparedImportInFlight = false; error = result; preparedImportError = result
                if (result.isEmpty()) {
                    retainedPreparedOutput = ""
                    getSharedPreferences("preparation-imports", MODE_PRIVATE).edit().putString("preparedImported", destination).remove("pendingPrepared").apply()
                    detail = "Prepared firmware imported as a new device"; refreshDevices(); firmwareExpectedExit = true; firmware?.finish()
                }
            }
        }, "prepared-device-import").start()
    }
    private fun importStockIpsw(uri: Uri) {
        if (preparationActive() || busy) return
        busy = true
        Thread({
            try {
                val target = File(filesDir, "stock-${System.nanoTime()}.ipsw")
                contentResolver.openInputStream(uri)?.use { input -> target.outputStream().use { input.copyTo(it) } } ?: error("Cannot read stock IPSW")
                handler.post { ipswPath = target.absolutePath; ipswSelected = true; busy = false; getSharedPreferences("preparation-imports", MODE_PRIVATE).edit().putString("ipsw", ipswPath).apply() }
            } catch (e: Exception) { handler.post { error = e.message ?: "IPSW import failed"; busy = false } }
        }, "stock-ipsw-import").start()
    }
    private fun importTrustedTools(uri: Uri) {
        if (preparationActive() || busy) return
        busy = true
        Thread({
            val target = File(filesDir, "verified-tools-${System.nanoTime()}")
            try {
                val source = DocumentFile.fromTreeUri(this, uri) ?: error("Cannot read trusted tools folder")
                val inventory = source.findFile("inventory.json") ?: error("Verified inventory.json missing")
                val guest = source.findFile("release-guest-unpacked")?.findFile("guest-tools") ?: error("Verified guest-tools folder missing")
                check(target.mkdirs())
                contentResolver.openInputStream(inventory.uri)?.use { input -> File(target, "inventory.json").outputStream().use { input.copyTo(it) } } ?: error("Cannot read verified inventory")
                val exported = File(target, "release-guest-unpacked").apply { check(mkdir()) }
                copyTree(guest, File(exported, "guest-tools"))
                // Copy required tooling only. Keep fixtures, SDKs and captures in
                // their original location; supplemental n81 NAND can be 18 GiB.
                handler.post { toolsPath = target.absolutePath; toolsSelected = true; busy = false; getSharedPreferences("preparation-imports", MODE_PRIVATE).edit().putString("tools", toolsPath).apply() }
            } catch (e: Exception) { target.deleteRecursively(); handler.post { error = e.message ?: "Tools import failed"; busy = false } }
        }, "trusted-tools-import").start()
    }
    private fun refreshDevices() {
        Thread({
            try {
                val loaded = engine?.records()?.toList() ?: emptyList()
                handler.post { devices = loaded; selected = loaded.firstOrNull { it.getBoolean("selected") } }
            } catch (e: RemoteException) { handler.post { error = "Device library unavailable: ${e.message}" } }
        }, "device-library").start()
    }
    private fun bindWorker() {
        workerEvidenceDelivery.advance()
        if (bound) { unbindService(connection); bound = false }
        // BIND_IMPORTANT gives the worker this activity's scheduling group while
        // it is on screen (top-app: every core, fast frequency ramp). Without it
        // the worker sits in foreground, which on Pixel excludes the prime core
        // and leaves the vCPU's cores near minimum frequency.
        bound = bindService(Intent(this, EngineService::class.java), connection, BIND_AUTO_CREATE or BIND_IMPORTANT)
    }
    private fun workerExited() {
        val evidenceGeneration = workerEvidenceDelivery.retire(engine != null || workerPid != 0) ?: return
        val unexpected = !engineExpectedExit && (guestStartRequested || sessionOwned || status in listOf("running", "starting", "paused"))
        val exitedPid = workerPid
        engine = null; resetPerformanceObservation(); workerPid = 0; sessionOwned = false; displayAsleep = false; stopSensors(); displayView?.keepScreenOn = false
        status = if (unexpected) "failed" else "stopped"
        detail = ""
        if (unexpected) error = "Guest worker exited unexpectedly; writable state retained. Start creates a fresh worker. Private diagnostics are retained."
        else if (engineExpectedExit) error = "Guest worker disposed; writable state retained. Shutdown confirmation receipt is unavailable."
        Thread({
            WorkerExitEvidence.capture(applicationContext, exitedPid)
            val terminal = try {
                val file = File(filesDir, "worker-shutdown-$exitedPid.json")
                file.takeIf { it.exists() }?.readText()?.let { JSONObject(it) }?.takeIf { it.optInt("workerPid") == exitedPid }
            } catch (_: Exception) { null }
            val retained = try { File(filesDir, "worker-stop-error-$exitedPid.txt").takeIf { it.exists() }?.readText() } catch (_: java.io.IOException) { null }
            if (terminal != null) {
                val reported = terminal.optString("error")
                if (reported.isNotEmpty()) handler.post { workerEvidenceDelivery.deliver(evidenceGeneration) { error = reported } }
                else if (!unexpected && terminal.optString("reason") == "native-terminal" && terminal.optString("nativeTerminalState") == "stopped") {
                    handler.post { workerEvidenceDelivery.deliver(evidenceGeneration) { error = "" } } // Own current terminal report wins over an earlier error.
                }
            } else retained?.takeIf { it.isNotEmpty() }?.let { handler.post { workerEvidenceDelivery.deliver(evidenceGeneration) { error = it } } }
        }, "worker-retained-error").start()
    }
    private fun startGuest(coldRecovery: Boolean = false, retainedRequest: GuestStartRequest? = null) {
        if (engineRestarting) return
        val request = retainedRequest ?: GuestStartRequest(
            selected?.getString("id") ?: run { error = "Select a device"; status = "stopped"; return }, coldRecovery)
        // Preserve the exact record and explicit recovery choice across worker
        // disposal/rebinding; later device-list refreshes cannot change the request.
        pendingGuestRequest = request
        val worker = engine
        if (worker == null) { pendingGuestStart = true; status = "starting"; bindWorker(); return }
        if (sessionOwned) { restartGuestForStart(worker); return }
        workerEvidenceDelivery.advance()
        engineExpectedExit = false; guestStartRequested = true; status = "starting"
        startForegroundService(Intent(this, EngineService::class.java))
        Thread({
            try {
                val b = if (request.coldRecovery) worker.recoverStart(request.recordId) else worker.start(request.recordId)
                handler.post { if (engine?.asBinder() == worker.asBinder()) error = b.getString("error") ?: "" }
            }
            catch (failure: Exception) { handler.post {
                if (engine?.asBinder() == worker.asBinder()) {
                    error = "Guest start failed: ${failure.message ?: "worker exited"}. Writable state and private diagnostics retained."
                    status = "failed"
                }
            } }
        }, "guest-start").start()
    }
    private fun stopGuest() {
        val worker = engine ?: return
        engineExpectedExit = true; status = "stopping"; stopSensors()
        Thread({
            try { worker.stop() }
            catch (failure: RemoteException) { handler.post {
                if (engine?.asBinder() == worker.asBinder()) error = "Stop admission failed; writable state retained: ${failure.message ?: "worker exited"}"
            } }
        }, "guest-stop-request").start()
    }
    private fun restartGuestForStart(old: IEngine) {
        workerEvidenceDelivery.advance()
        engineRestarting = true; engineExpectedExit = true; pendingGuestStart = true; status = "stopping"
        if (bound) { unbindService(connection); bound = false }
        val binder = old.asBinder()
        Thread({
            val died = CountDownLatch(1); val recipient = IBinder.DeathRecipient { died.countDown() }
            try {
                if (binder.isBinderAlive) {
                    try { binder.linkToDeath(recipient, 0) } catch (_: DeadObjectException) { died.countDown() }
                    try { old.stop() } catch (_: DeadObjectException) { died.countDown() }
                    check(!binder.isBinderAlive || died.await(45, TimeUnit.SECONDS)) { "Previous guest worker has not exited. Retry after it stops." }
                }
                handler.post { engine = null; resetPerformanceObservation(); sessionOwned = false; engineRestarting = false; bindWorker() }
            } catch (failure: Exception) { handler.post { engineRestarting = false; pendingGuestStart = false; error = failure.message ?: "Guest worker restart failed"; status = "failed" } }
            finally { try { binder.unlinkToDeath(recipient, 0) } catch (_: java.util.NoSuchElementException) { } }
        }, "guest-worker-restart").start()
    }
    private fun pulse(button: Int) {
        val worker = engine ?: return
        try {
            worker.button(button, true)
            handler.postDelayed({
                try { worker.button(button, false) }
                catch (_: RemoteException) { error = "Guest worker exited during button release; Start a fresh worker." }
            }, 100)
        } catch (_: RemoteException) { error = "Guest worker unavailable; Start a fresh worker." }
    }
    private fun capture() {
        val path = File(filesDir, "screenshots/guest-${System.currentTimeMillis()}.png").absolutePath
        Thread({
            try {
                val result = engine?.screenshot(path) ?: "Worker unavailable"
                require(result.isEmpty()) { result }
                val values = ContentValues().apply {
                    put(MediaStore.Images.Media.DISPLAY_NAME, File(path).name)
                    put(MediaStore.Images.Media.MIME_TYPE, "image/png")
                    put(MediaStore.Images.Media.RELATIVE_PATH, "Pictures/LightTouch")
                    put(MediaStore.Images.Media.IS_PENDING, 1)
                }
                val uri = contentResolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values) ?: error("Cannot create screenshot")
                try {
                    contentResolver.openOutputStream(uri)?.use { output -> File(path).inputStream().use { it.copyTo(output) } } ?: error("Cannot write screenshot")
                    values.clear(); values.put(MediaStore.Images.Media.IS_PENDING, 0)
                    contentResolver.update(uri, values, null, null)
                } catch (e: Exception) { contentResolver.delete(uri, null, null); throw e }
                handler.post { captureNotice = "Screenshot saved in Pictures/LightTouch" }
            } catch (e: Exception) { handler.post { error = e.message ?: "Screenshot failed" } }
        }, "screenshot").start()
    }
    private fun copyAsset(uri: Uri) {
        val targetName = assetName
        busy = true
        Thread({
            try {
                val target = File(filesDir, targetName)
                val temporary = File(filesDir, "$targetName.partial")
                contentResolver.openInputStream(uri)?.use { input -> temporary.outputStream().use { input.copyTo(it) } } ?: error("Cannot read file")
                check(temporary.renameTo(target)) { "Could not publish imported file" }
                handler.post { detail = "Imported $targetName"; busy = false }
            } catch (e: Exception) { handler.post { error = e.message ?: "Import failed"; busy = false } }
        }, "asset-import").start()
    }
    private fun importBaseArchive(uri: Uri) {
        busy = true
        detail = "Copying and validating prepared base archive"
        Thread({
            val staging = File(filesDir, "base-import-${System.nanoTime()}.itbase")
            try {
                contentResolver.openInputStream(uri)?.use { input -> staging.outputStream().use { input.copyTo(it) } } ?: error("Cannot read selected base")
                val result = engine?.importBase(staging.absolutePath, DocumentFile.fromSingleUri(this, uri)?.name ?: "Imported device") ?: error("Worker unavailable")
                require(result.isEmpty()) { result }
                handler.post { detail = "Prepared base validated and imported as a new device"; busy = false; refreshDevices() }
            } catch (e: Exception) { handler.post { error = e.message ?: "Import failed"; busy = false } }
            finally { staging.delete() }
        }, "base-archive-import").start()
    }
    private fun importDirectory(uri: Uri) {
        busy = true
        Thread({
            val staging = File(filesDir, "import-${System.nanoTime()}")
            try {
                val source = DocumentFile.fromTreeUri(this, uri) ?: error("Cannot read selected directory")
                copyTree(source, staging)
                val result = engine?.importBase(staging.absolutePath, source.name ?: "Imported device") ?: error("Worker unavailable")
                require(result.isEmpty()) { result }
                handler.post { detail = "Prepared base validated and imported as a new device"; busy = false; refreshDevices() }
            } catch (e: Exception) { handler.post { error = e.message ?: "Import failed"; busy = false } }
            finally { staging.deleteRecursively() }
        }, "base-import").start()
    }
    private fun copyTree(source: DocumentFile, destination: File) {
        check(destination.mkdirs())
        for (child in source.listFiles()) {
            val name = child.name ?: error("Missing entry name")
            require(name != "." && name != ".." && '/' !in name) { "Invalid entry name" }
            val target = File(destination, name)
            if (child.isDirectory) copyTree(child, target)
            else contentResolver.openInputStream(child.uri)?.use { input -> target.outputStream().use { input.copyTo(it) } } ?: error("Cannot read $name")
        }
    }
    private var listening = false
    // Sensor callbacks arrive here, not on the UI thread; motion() is a oneway Binder call.
    private val sensorThread by lazy { HandlerThread("guest-motion").apply { start() } }
    @Volatile private var motionWorker: IEngine? = null
    @Volatile private var displayRotation = 0
    // Last accelerometer sample sent (m/s², device axes), shown beside the switch at a few Hz so tilt can be checked by eye.
    @Volatile private var lastAcceleration: FloatArray? = null
    private var tiltReadout by mutableStateOf("")
    private val tiltReadoutTick = object : Runnable {
        override fun run() {
            tiltReadout = lastAcceleration?.let { v -> String.format(Locale.ROOT, "x %+.1f  y %+.1f  z %+.1f", v[0], v[1], v[2]) } ?: ""
            if (listening) handler.postDelayed(this, 250)
        }
    }
    /** Only the sensors the guest board models are opened; the 2G has an accelerometer and no gyroscope. */
    private fun startSensors() {
        val record = selected
        if (!tiltEnabled || listening || record == null) return
        motionWorker = engine
        displayRotation = (display?.rotation ?: 0) * 90
        val thread = Handler(sensorThread.looper)
        // 100 Hz: the runtime admits up to 200 Hz per stream. Units are already m/s² and rad/s;
        // axis and rotation mapping into guest portrait axes is native (lt_map_motion).
        if (record.getBoolean("acceleration")) sensors.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)?.let { sensors.registerListener(this, it, 10_000, thread) }
        if (record.getBoolean("gyro")) sensors.getDefaultSensor(Sensor.TYPE_GYROSCOPE)?.let { sensors.registerListener(this, it, 10_000, thread) }
        listening = true
        handler.removeCallbacks(tiltReadoutTick); handler.post(tiltReadoutTick)
    }
    private fun stopSensors() { sensors.unregisterListener(this); listening = false; motionWorker = null; lastAcceleration = null; tiltReadout = "" }
    override fun onSensorChanged(event: SensorEvent) {
        val kind = if (event.sensor.type == Sensor.TYPE_GYROSCOPE) 2 else 1
        if (kind == 1) lastAcceleration = event.values.copyOf(3)
        try { motionWorker?.motion(kind, event.timestamp, event.values.copyOf(3), displayRotation) } catch (_: RemoteException) { }
    }
    private fun setTilt(on: Boolean) {
        tiltEnabled = on
        getSharedPreferences("ui", MODE_PRIVATE).edit().putBoolean("tilt", on).apply()
        if (!on) stopSensors() else if (status == "running") startSensors()
    }
    private fun sendButton(id: Int, down: Boolean) {
        val worker = engine ?: return
        buttonCalls.execute { try { worker.button(id, down) } catch (_: RemoteException) { } }
    }
    /** Device view: the guest in a drawn device, system bars hidden, held in portrait like the real device. */
    private fun showDeviceView(on: Boolean) {
        fullscreen = on
        getSharedPreferences("ui", MODE_PRIVATE).edit().putBoolean("fullscreen", on).apply()
        applyWindowMode()
    }
    private fun applyWindowMode() {
        val on = fullscreen
        requestedOrientation = if (on) ActivityInfo.SCREEN_ORIENTATION_PORTRAIT else ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
        if (Build.VERSION.SDK_INT >= 30) window.insetsController?.let { controller ->
            if (on) { controller.hide(PlatformInsets.Type.systemBars()); controller.systemBarsBehavior = WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE }
            else controller.show(PlatformInsets.Type.systemBars())
        } else @Suppress("DEPRECATION") {
            window.decorView.systemUiVisibility = if (on) (android.view.View.SYSTEM_UI_FLAG_FULLSCREEN or android.view.View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or
                android.view.View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or android.view.View.SYSTEM_UI_FLAG_LAYOUT_STABLE) else 0
        }
    }
    private fun recordTarget(): StoreTarget? = selected?.let { firmwareTargets["${it.getString("board")}-${it.getString("build")}"] }
    private fun openStore() {
        storeOpen = true
        store.close(); store.dismissJob() // Reopen on the search, not a finished install's details.
        store.useRecord(recordTarget())
        if (muxSocket.isNotEmpty()) store.refreshGuest()
    }
    override fun onAccuracyChanged(sensor: Sensor, accuracy: Int) = Unit
    override fun onStart() { super.onStart(); active = true; DisplayOwners.active.started(this); displayView?.keepScreenOn = status in listOf("starting", "running", "stopping"); handler.post(poll); resetPerformanceObservation() }
    override fun onResume() {
        super.onResume(); displayResumed = true
        displayOwnerTicket = DisplayOwners.active.resumed(this, hasWindowFocus())
        displayView?.attachActiveSurface()
    }
    override fun onPostResume() {
        super.onPostResume()
        displayOwnerTicket = DisplayOwners.active.focusChanged(this, hasWindowFocus())
        displayView?.attachActiveSurface()
    }
    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) applyWindowMode() // Transient bars shown by a swipe hide again.
        // Consult actual Activity focus too: an old callback alone is not authority.
        displayOwnerTicket = DisplayOwners.active.focusChanged(this, hasFocus && hasWindowFocus())
        displayView?.attachActiveSurface()
    }
    override fun onPause() {
        displayResumed = false; DisplayOwners.active.paused(this)
        displayView?.cancelOwnedGesture(); super.onPause()
    }
    override fun onStop() {
        val wasOwner = ownsDisplay()
        active = false; displayView?.keepScreenOn = false; stopSensors()
        // Owner validation also occurs inside the native guard; a handoff after
        // this UI check still cannot make old A pause/cancel/detach new B.
        if (wasOwner && ((sessionOwned && status !in listOf("stopping", "stopped", "failed")) || status in listOf("running", "starting")))
            displayView?.backgroundOwnedDisplay()
        else displayView?.retireDisplay()
        DisplayOwners.active.stopped(this, displayOwnerTicket)
        handler.removeCallbacks(poll); resetPerformanceObservation(); super.onStop()
    }
    override fun onDestroy() { buttonCalls.shutdown(); displayView?.retireDisplay(); DisplayOwners.active.stopped(this, displayOwnerTicket); workerEvidenceDelivery.advance(); handler.removeCallbacksAndMessages(null); stopSensors(); if (bound) unbindService(connection); if (firmwareBound) unbindService(firmwareConnection); super.onDestroy() }
}

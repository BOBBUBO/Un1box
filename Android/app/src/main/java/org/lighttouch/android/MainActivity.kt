package org.lighttouch.android

import android.content.*
import android.hardware.*
import android.net.Uri
import android.provider.MediaStore
import org.json.JSONObject
import android.os.*
import android.content.pm.ActivityInfo
import android.view.KeyEvent
import android.view.WindowInsets as PlatformInsets
import android.view.WindowInsetsController
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.*
import androidx.compose.ui.unit.dp
import androidx.core.view.WindowCompat
import androidx.documentfile.provider.DocumentFile
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.Executors

@OptIn(androidx.compose.ui.ExperimentalComposeUiApi::class)
class MainActivity : ComponentActivity(), SensorEventListener, GuestHost {
    private var engine by mutableStateOf<IEngine?>(null)
    override var status by mutableStateOf("stopped")
        private set
    private var detail by mutableStateOf("")
    override var displayAsleep by mutableStateOf(false)
        private set
    override var error by mutableStateOf("")
        private set
    private val workerEvidenceDelivery = SessionDeliveryGate()
    private var workerPid by mutableIntStateOf(0)
    override var sessionOwned by mutableStateOf(false)
        private set
    private data class GuestStartRequest(val recordId: String, val coldRecovery: Boolean)
    private var pendingGuestRequest: GuestStartRequest? = null
    private var pendingGuestStart = false
    private var guestStartRequested = false
    private var engineExpectedExit = false
    private var engineRestarting = false
    override var busy by mutableStateOf(false)
        private set
    /** A passing message (screenshot saved, device renamed), shown as a snackbar. */
    private var notice by mutableStateOf("")
    override var profileRecording by mutableStateOf(false)
        private set
    override var profileAutomatic by mutableStateOf(false)
        private set
    override var profileBusy by mutableStateOf(false)
        private set
    override var profileNotice by mutableStateOf("")
        private set
    private var licensesOpen by mutableStateOf(false)
    private var devicesOpen by mutableStateOf(false)
    private var muxSocket by mutableStateOf("")
    private var appServicesError by mutableStateOf("")
    private val store by lazy { StoreModel(applicationContext) { muxSocket.ifEmpty { null } } }
    private val localIpa = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> if (uri != null) store.installLocal(uri) }
    // board-build -> (product type, iOS version), from the bundled firmware catalog.
    @Volatile private var firmwareTargets: Map<String, StoreTarget> = emptyMap()
    private var catalogVersion by mutableIntStateOf(0)
    // Hardware buttons are Binder calls; one thread keeps down/up ordered and off the UI thread.
    private val buttonCalls = Executors.newSingleThreadExecutor { Thread(it, "guest-buttons") }
    private var records by mutableStateOf<List<Bundle>>(emptyList())
    private var recordsLoaded by mutableStateOf(false)
    override val devices: List<DeviceSummary> get() { catalogVersion; return records.map { DeviceSummary.of(it, firmwareTargets) } }
    override val selected: DeviceSummary? get() = devices.firstOrNull { it.selected }
    override val settings by lazy { AppSettings(this) }
    private val monitor = PerformanceMonitor { engine }
    override val perf: PerfSnapshot get() = monitor.snapshot
    override val storeInstalling get() = store.installing
    // Save & Stop needs the engine to serialize live GL state on EGL hosts (docs/TODO.md).
    override val canSaveState = false
    /** HOME or FULLSCREEN: what is under any full-page overlay. */
    private var base by mutableStateOf(Screen.HOME)
    /** LOADING, SETUP, SETTINGS, STORE or HITCH drawn over [base]; null shows the guest. */
    private var overlay by mutableStateOf<Screen?>(Screen.LOADING)
    private val setup = SetupState()
    private var hitchData by mutableStateOf<HitchData?>(null)
    private var hitchError by mutableStateOf<String?>(null)
    private var hitchSaving by mutableStateOf(false)
    private var hitchNotice by mutableStateOf<String?>(null)
    private var hitchReport by mutableStateOf<Uri?>(null)
    private var hitchResume = false
    // The guest was running when LightTouch left the screen; resume it on return (settings.autoResume).
    private var pausedByBackground = false
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
    override fun toggleProfile() {
        val worker = engine ?: return
        val recording = !profileRecording
        profileBusy = true
        Thread({
            val result = try { worker.profile(recording) }
                catch (e: Exception) { Bundle().apply { putString("profileError", e.message ?: "Profile capture failed") } }
            handler.post {
                if (engine?.asBinder() == worker.asBinder()) {
                    profileRecording = result.getBoolean("profileRecording")
                    profileNotice = result.getString("profileNotice") ?: ""
                    result.getString("profileError")?.takeIf { it.isNotEmpty() }?.let { error = it }
                }
                profileBusy = false
            }
        }, "profile-control").start()
    }
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
            refreshDevices()
            applySettings()
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
                            val reportedError = b.getString("profileError")?.takeIf { it.isNotEmpty() } ?: b.getString("error") ?: ""
                            if (reportedError.isNotEmpty() || status == "running") error = reportedError
                            workerPid = b.getInt("pid")
                            profileRecording = b.getBoolean("profileRecording"); profileAutomatic = b.getBoolean("profileAutomatic")
                            profileNotice = b.getString("profileNotice") ?: ""
                            sessionOwned = b.getBoolean("sessionStarted")
                            val socket = if (status == "running") b.getString("usbMuxSocket") ?: "" else ""
                            if (socket != muxSocket) { muxSocket = socket; if (socket.isNotEmpty() && overlay == Screen.STORE) store.refreshGuest() }
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
    /**
     * Run the panel at 60 Hz while LightTouch is in front, the way Google Maps does. The guest is a
     * 60 Hz device: at 120 Hz each of its frames must also land on the right one of two refreshes,
     * which the presenter can only keep by tracking the display's phase (frame_pacer.h), and the
     * Pixel 10 kept 120 Hz despite the guest surface's 60 Hz fixed-source request. A preferred
     * display mode is the window's own request, which the system grants the foreground window
     * whatever the Smooth Display setting. Only a 60 Hz mode at the current resolution is taken.
     *
     * Touch boost (Android 15+) is off for the same reason: touch is constant in a game, and a boost
     * would raise the panel to its top rate. Both apply only while the guest is what is on screen;
     * the Store and the dialogs over it are ordinary scrolling UI, which at a fixed 60 Hz with no
     * boost looked choppy beside the rest of a 120 Hz phone, so they get the system's own choice.
     */
    private var sixtyHertzMode = -1
    private fun preferSixtyHertz(guestInFront: Boolean) {
        if (sixtyHertzMode < 0) {
            val current = (if (Build.VERSION.SDK_INT >= 30) display else @Suppress("DEPRECATION") windowManager.defaultDisplay) ?: return
            val size = current.mode
            sixtyHertzMode = current.supportedModes
                .filter { it.physicalWidth == size.physicalWidth && it.physicalHeight == size.physicalHeight }
                .minByOrNull { kotlin.math.abs(it.refreshRate - 60f) }
                ?.takeIf { kotlin.math.abs(it.refreshRate - 60f) < 1f }?.modeId ?: 0
        }
        val mode = if (guestInFront) sixtyHertzMode else 0
        if (window.attributes.preferredDisplayModeId != mode) window.attributes = window.attributes.apply { preferredDisplayModeId = mode }
        if (Build.VERSION.SDK_INT >= 35) window.setFrameRateBoostOnTouchEnabled(!guestInFront)
    }
    private val pickFolder = registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri -> if (uri != null) scanFolder(uri) }
    private val pickSystem = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> if (uri != null) setup.system = picked(uri) }
    private val pickTools = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> if (uri != null) setup.tools = picked(uri) }
    private val pickRom = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> if (uri != null) setup.rom = picked(uri) }
    private val pickGuestToolsFile = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> if (uri != null) copyAsset(uri, "guest.itpack") }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // The guest is a portrait device; its landscape apps rotate inside the portrait screen.
        requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
        WindowCompat.setDecorFitsSystemWindows(window, false)
        // Draw under transparent bars; the theme picks light or dark bar icons.
        @Suppress("DEPRECATION") run { window.statusBarColor = android.graphics.Color.TRANSPARENT; window.navigationBarColor = android.graphics.Color.TRANSPARENT }
        window.isNavigationBarContrastEnforced = false; window.isStatusBarContrastEnforced = false
        preferSixtyHertz(guestInFront = false)
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
        Thread({
            try {
                val entries = JSONObject(assets.open("firmware-catalog.json").bufferedReader().use { it.readText() }).getJSONArray("entries")
                firmwareTargets = (0 until entries.length()).map { entries.getJSONObject(it) }
                    .filter { it.has("product_type") && it.has("version") }
                    .associate { "${it.getString("board")}-${it.getString("build")}" to StoreTarget.of(it.getString("product_type"), it.getString("version")) }
                handler.post { catalogVersion++; if (overlay == Screen.STORE) store.useRecord(recordTarget()); if (engine != null) refreshDevices() }
            } catch (_: Exception) { /* The Store falls back to the running guest's own report. */ }
        }, "store-targets").start()
        bindWorker()
        setContent {
            val guestInFront = overlay == null && !devicesOpen && !licensesOpen && !preparationOpen && status == "running"
            LaunchedEffect(guestInFront) { preferSixtyHertz(guestInFront) }
            // A full page hides the guest surface: the presenter stops drawing into it and drops its 60 Hz request.
            val guestCovered = overlay in listOf(Screen.LOADING, Screen.SETUP, Screen.SETTINGS, Screen.STORE)
            LaunchedEffect(guestCovered, engine) {
                val worker = engine ?: return@LaunchedEffect
                Thread({ try { worker.setPresentVisible(!guestCovered) } catch (_: RemoteException) { } }, "present-visibility").start()
            }
            val immersive = base == Screen.FULLSCREEN && overlay == null
            LaunchedEffect(immersive) { applyWindowMode(immersive) }
            // A guest that stops while full screen drops back to the device view.
            LaunchedEffect(status) { if (base == Screen.FULLSCREEN && status in listOf("stopped", "failed")) base = Screen.HOME }
            // First run: no devices yet, so set one up.
            LaunchedEffect(recordsLoaded, records.isEmpty()) {
                if (recordsLoaded && overlay == Screen.LOADING) overlay = if (records.isEmpty()) Screen.SETUP.also { setup.reset(firstRun = true) } else null
            }
            val showOverlay = settings.overlay != OverlayLevel.OFF && status == "running" &&
                ((overlay == null && (base == Screen.FULLSCREEN || settings.overlayInDeviceView)) || overlay == Screen.SETTINGS)
            LaunchedEffect(showOverlay, settings.overlay) { monitor.setActive(showOverlay, settings.overlay == OverlayLevel.FULL) }
            LightTouchTheme(settings.theme) {
                val dark = MaterialTheme.colorScheme.background.luminance() < 0.5f
                LaunchedEffect(dark, immersive) {
                    WindowCompat.getInsetsController(window, window.decorView).apply {
                        isAppearanceLightStatusBars = !dark && !immersive; isAppearanceLightNavigationBars = !dark && !immersive
                    }
                }
                // One SurfaceView moves between the device and full-screen layouts, keeping its display lease.
                val guestSurface = remember { movableContentOf { modifier: Modifier ->
                    WorkerGuestSurface(worker = engine, currentWorker = { engine },
                        mayClaim = { view -> mayClaimDisplay() && displayView === view },
                        ownerTicket = { displayOwnerTicket }, onError = { error = it },
                        onCreated = { displayView = it },
                        onReleased = { view -> if (displayView === view) displayView = null },
                        keepScreenOn = active && status in listOf("starting", "running", "stopping"),
                        modifier = modifier.background(Color.Black).testTag("guest_display"))
                } }
                val snackbar = remember { SnackbarHostState() }
                LaunchedEffect(notice) { if (notice.isNotEmpty()) { snackbar.showSnackbar(notice); notice = "" } }
                Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
                    // The guest layout stays composed under full-page overlays so its Surface and lease survive them.
                    if (base == Screen.FULLSCREEN) FullscreenScreen(this@MainActivity, guestSurface) else HomeScreen(this@MainActivity, guestSurface)
                    when (overlay) {
                        Screen.LOADING -> Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background), contentAlignment = Alignment.Center) {
                            CircularProgressIndicator(Modifier.size(32.dp), strokeWidth = 3.dp)
                        }
                        Screen.SETUP -> SetupWizard(setup, canCancel = records.isNotEmpty(),
                            onPickFolder = { pickFolder.launch(null) }, onPickSystem = { pickSystem.launch(arrayOf("application/octet-stream", "*/*")) },
                            onPickTools = { pickTools.launch(arrayOf("application/octet-stream", "*/*")) }, onPickRom = { pickRom.launch(arrayOf("application/octet-stream", "*/*")) },
                            onCreate = ::createDevice, onPrepare = { openPreparation() },
                            onFinish = { start -> overlay = null; if (start) startGuest() }, onCancel = { overlay = null })
                        Screen.SETTINGS -> SettingsScreen(this@MainActivity) { overlay = null }
                        Screen.STORE -> StoreScreen(store, running = status == "running" && muxSocket.isNotEmpty(),
                            onPickLocal = { localIpa.launch(arrayOf("application/octet-stream", "*/*")) }) { overlay = null }
                        Screen.HITCH -> HitchInspector(hitchData, hitchError, onResume = ::closeHitch, onSave = ::saveHitch, saving = hitchSaving,
                            savedNotice = hitchNotice, onShare = hitchReport?.let { uri -> { shareReport(uri) } })
                        else -> {}
                    }
                    SnackbarHost(snackbar, Modifier.align(Alignment.BottomCenter).windowInsetsPadding(WindowInsets.safeDrawing).padding(bottom = 96.dp))
                }
                if (devicesOpen) DevicesSheet(this@MainActivity) { devicesOpen = false }
                if (preparationOpen) PreparationDialog()
                if (licensesOpen) LicenseDialog { licensesOpen = false }
            }
        }
    }

    // GuestHost: the screens' requests.
    override fun dismissError() { error = "" }
    override fun start() = startGuest()
    override fun recover() = startGuest(coldRecovery = true)
    override fun stop() = stopGuest()
    override fun saveAndStop() = stopGuest()
    override fun setPaused(paused: Boolean) {
        val worker = engine ?: return
        if (paused) displayView?.cancelOwnedGesture()
        pausedByBackground = false
        try { worker.pause(paused) } catch (_: RemoteException) { error = "Guest worker unavailable" }
    }
    override fun button(id: Int, down: Boolean) = sendButton(id, down)
    override fun screenshot() = capture()
    override fun navigate(screen: Screen) {
        when (screen) {
            Screen.HOME, Screen.FULLSCREEN -> { base = screen; overlay = null }
            Screen.STORE -> openStore()
            else -> overlay = screen
        }
    }
    override fun openDevices() { refreshDevices(); devicesOpen = true }
    override fun addDevice() { setup.reset(firstRun = false); overlay = Screen.SETUP }
    override fun openLicenses() { licensesOpen = true }
    override fun openPreparation() { preparationOpen = true; bindFirmware() }
    override fun pickGuestTools() { pickGuestToolsFile.launch(arrayOf("application/octet-stream", "*/*")) }
    override fun applySettings() {
        if (!settings.tilt) stopSensors() else if (status == "running") startSensors()
        val worker = engine ?: return
        val scale = settings.scaling.native; val detailed = settings.detailedTiming
        Thread({ try { worker.setPresentScale(scale); worker.setDetailedTiming(detailed) } catch (_: RemoteException) { } }, "apply-settings").start()
    }
    override fun selectDevice(id: String) {
        busy = true
        val worker = engine
        Thread({
            val result = try { worker?.select(id) ?: "Worker unavailable" } catch (e: RemoteException) { e.message ?: "Device selection failed" }
            handler.post { error = result; busy = false; refreshDevices() }
        }, "device-selection").start()
    }
    override fun renameDevice(id: String, name: String) {
        val worker = engine ?: return
        Thread({
            val result = try { worker.rename(id, name) } catch (e: RemoteException) { e.message ?: "Rename failed" }
            handler.post { if (result.isEmpty()) notice = "Renamed to $name" else error = result; refreshDevices() }
        }, "device-rename").start()
    }
    override fun deleteDevice(id: String) {
        val worker = engine ?: return
        busy = true
        Thread({
            val result = try { worker.remove(id) } catch (e: RemoteException) { e.message ?: "Delete failed" }
            handler.post { busy = false; if (result.isEmpty()) notice = "Device deleted" else error = result; refreshDevices(afterDelete = true) }
        }, "device-delete").start()
    }

    /** Pause the guest, freeze the frame history and open the inspector over it. */
    override fun openHitch() {
        val worker = engine ?: return
        if (status !in listOf("running", "paused")) return
        hitchResume = status == "running"
        if (hitchResume) setPaused(true)
        hitchData = null; hitchError = null; hitchNotice = null; hitchReport = null
        overlay = Screen.HITCH
        Thread({
            try {
                val data = HitchData.load(worker.hitchSnapshot())
                handler.post { if (overlay == Screen.HITCH) hitchData = data }
            } catch (e: Exception) { handler.post { hitchError = e.message ?: "Frame history unavailable" } }
        }, "hitch-snapshot").start()
    }
    private fun closeHitch() {
        overlay = null; hitchData = null
        if (hitchResume) setPaused(false)
    }
    private fun saveHitch(from: Long, to: Long) {
        val data = hitchData ?: return
        hitchSaving = true
        Thread({
            try {
                val uri = saveHitchReport(applicationContext, data, from, to)
                handler.post { hitchSaving = false; hitchReport = uri; hitchNotice = "Saved to Download/LightTouch" }
            } catch (e: Exception) { handler.post { hitchSaving = false; hitchNotice = "Could not save: ${e.message}" } }
        }, "hitch-report").start()
    }
    private fun shareReport(uri: Uri) {
        startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("application/zip").putExtra(Intent.EXTRA_STREAM, uri)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION), "Share hitch report"))
    }

    // Setup wizard: find the files, copy them in, create and name the device.
    private fun displayName(uri: Uri) = DocumentFile.fromSingleUri(this, uri)?.name ?: uri.lastPathSegment ?: "file"
    private fun picked(uri: Uri) = PickedFile(uri, displayName(uri))
    private fun scanFolder(tree: Uri) {
        val root = DocumentFile.fromTreeUri(this, tree) ?: return
        Thread({
            // The folder itself, then one level down (e.g. LightTouch-assets/ inside the chosen folder).
            val candidates = root.listFiles().toList() + root.listFiles().filter { it.isDirectory }.flatMap { it.listFiles().toList() }
            val files = candidates.filter { it.isFile }
            val system = files.firstOrNull { it.name?.endsWith(".itbase", true) == true }?.let { PickedFile(it.uri, it.name ?: "") }
                ?: (listOf(root) + root.listFiles().filter { it.isDirectory }).firstOrNull { it.findFile("device.lock.json") != null }
                    ?.let { PickedFile(it.uri, (it.name ?: "folder") + "/", folder = true) }
            val tools = files.filter { it.name?.endsWith(".itpack", true) == true }.minByOrNull { if (it.name?.contains("armv6") == true) 0 else 1 }
                ?.let { PickedFile(it.uri, it.name ?: "") }
            val rom = files.firstOrNull { it.name?.lowercase()?.startsWith("bootrom") == true }?.let { PickedFile(it.uri, it.name ?: "") }
            handler.post {
                system?.let { setup.system = it }; tools?.let { setup.tools = it }; rom?.let { setup.rom = it }
                val found = listOfNotNull("iOS system".takeIf { system != null }, "guest tools".takeIf { tools != null }, "boot file".takeIf { rom != null })
                setup.scanned = if (found.isEmpty()) "Nothing found in ${root.name}" else "Found " + found.joinToString(", ") + " in ${root.name}"
            }
        }, "setup-scan").start()
    }
    /** Copies [uri] to [target] and returns the copy's SHA-256 (hex). */
    private fun copyWithProgress(uri: Uri, target: File, label: String): String {
        val total = DocumentFile.fromSingleUri(this, uri)?.length() ?: 0L
        val digest = java.security.MessageDigest.getInstance("SHA-256")
        contentResolver.openInputStream(uri)?.use { input -> target.outputStream().use { output ->
            val buffer = ByteArray(1 shl 20); var done = 0L; var shown = -1L
            while (true) {
                val n = input.read(buffer); if (n < 0) break
                output.write(buffer, 0, n); digest.update(buffer, 0, n); done += n
                val percent = if (total > 0) done * 100 / total else -1
                if (percent != shown) { shown = percent; handler.post { setup.phase = if (percent >= 0) "$label… $percent%" else "$label… ${done shr 20} MB" } }
            }
        } } ?: error("Cannot read $label")
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
    private fun createDevice() {
        val system = setup.system ?: return
        val worker = engine ?: run { setup.error = "LightTouch is still starting; try again in a moment."; return }
        val tools = setup.tools; val rom = setup.rom; val requested = setup.name.trim()
        val before = records.mapNotNull { it.getString("id") }.toSet()
        setup.step = SetupStep.CREATING; setup.error = ""; setup.phase = ""; busy = true
        Thread({
            val staging = File(filesDir, "setup-${System.nanoTime()}").apply { mkdirs() }
            try {
                val romPath = rom?.let { File(staging, "bootrom.bin").also { f -> copyWithProgress(it.uri, f, "Copying the boot file") }.absolutePath } ?: ""
                val toolsPath = tools?.let { File(staging, "guest.itpack").also { f -> copyWithProgress(it.uri, f, "Copying guest tools") }.absolutePath } ?: ""
                var sha256 = ""
                val source = if (system.folder) File(staging, "base").also { handler.post { setup.phase = "Copying the iOS system…" }
                    copyTree(DocumentFile.fromTreeUri(this, system.uri) ?: error("Cannot read the folder"), it) }
                    else File(staging, "system.itbase").also { sha256 = copyWithProgress(system.uri, it, "Copying the iOS system") }
                handler.post { setup.phase = "Setting up the iOS system…" }
                val started = System.nanoTime()
                val result = worker.importDevice(source.absolutePath, "", romPath, toolsPath, sha256)
                android.util.Log.i("LightTouch", "Device import took ${(System.nanoTime() - started) / 1_000_000} ms")
                require(result.isEmpty()) { result }
                val all = worker.records().toList()
                val created = all.firstOrNull { it.getString("id") !in before } ?: error("The new device did not appear in the library")
                val id = created.getString("id") ?: ""
                val name = requested.ifEmpty { defaultDeviceName(created.getString("board") ?: "", created.getString("build") ?: "", firmwareTargets,
                    all.filter { it.getString("id") != id }.mapNotNull { it.getString("name") }) }
                worker.rename(id, name).takeIf { it.isNotEmpty() }?.let { error(it) }
                worker.select(id).takeIf { it.isNotEmpty() }?.let { error(it) }
                handler.post { setup.created = name; setup.step = SetupStep.DONE; refreshDevices() }
            } catch (e: Exception) {
                handler.post { setup.error = e.message ?: "Could not create the device"; setup.step = SetupStep.FILES }
            } finally { staging.deleteRecursively(); handler.post { busy = false } }
        }, "setup-create").start()
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        val volume = when (keyCode) { KeyEvent.KEYCODE_VOLUME_UP -> GuestButton.VOLUME_UP; KeyEvent.KEYCODE_VOLUME_DOWN -> GuestButton.VOLUME_DOWN; else -> -1 }
        if (volume >= 0 && settings.volumeKeys && status == "running" && overlay == null && !devicesOpen) {
            if (event.repeatCount == 0) sendButton(volume, true)
            return true
        }
        return super.onKeyDown(keyCode, event)
    }
    override fun onKeyUp(keyCode: Int, event: KeyEvent): Boolean {
        val volume = when (keyCode) { KeyEvent.KEYCODE_VOLUME_UP -> GuestButton.VOLUME_UP; KeyEvent.KEYCODE_VOLUME_DOWN -> GuestButton.VOLUME_DOWN; else -> -1 }
        if (volume >= 0 && settings.volumeKeys && status == "running" && overlay == null && !devicesOpen) { sendButton(volume, false); return true }
        return super.onKeyUp(keyCode, event)
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
            val result = try { engine?.importPrepared(destination, defaultDeviceName(firmwareId.substringBefore('-'), firmwareId.substringAfter('-'), firmwareTargets, devices.map { it.name })) ?: "Device worker unavailable" }
                catch (e: RemoteException) { e.message ?: "Device worker exited during import" }
            handler.post {
                busy = false; preparedImportInFlight = false; error = result; preparedImportError = result
                if (result.isEmpty()) {
                    retainedPreparedOutput = ""
                    getSharedPreferences("preparation-imports", MODE_PRIVATE).edit().putString("preparedImported", destination).remove("pendingPrepared").apply()
                    notice = "Prepared firmware imported as a new device"; refreshDevices(); firmwareExpectedExit = true; firmware?.finish()
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
    private fun refreshDevices(afterDelete: Boolean = false) {
        Thread({
            try {
                var loaded = engine?.records()?.toList() ?: return@Thread
                // Earlier builds named devices after their firmware id ("n72ap-7E18 prepared"); give those the friendly default once.
                if (firmwareTargets.isNotEmpty()) {
                    val names = loaded.mapNotNull { it.getString("name") }.toMutableList()
                    val renamed = loaded.count { record ->
                        val name = record.getString("name") ?: ""; val board = record.getString("board") ?: ""; val build = record.getString("build") ?: ""
                        if (!isGeneratedDeviceName(name, board, build)) return@count false
                        val friendly = defaultDeviceName(board, build, firmwareTargets, names - name)
                        friendly != name && engine?.rename(record.getString("id"), friendly).isNullOrEmpty().also { if (it) { names -= name; names += friendly } }
                    }
                    if (renamed > 0) loaded = engine?.records()?.toList() ?: loaded
                }
                handler.post {
                    records = loaded; recordsLoaded = true
                    if (afterDelete && loaded.isEmpty()) { setup.reset(firstRun = false); overlay = Screen.SETUP }
                }
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
        profileRecording = false; profileBusy = false
        engine = null; workerPid = 0; sessionOwned = false; displayAsleep = false; stopSensors(); displayView?.keepScreenOn = false
        status = if (unexpected) "failed" else "stopped"
        detail = ""
        if (unexpected) error = "Guest worker exited unexpectedly; writable state retained. Start creates a fresh worker. Private diagnostics are retained."
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
            selected?.id ?: run { error = "Select a device"; status = "stopped"; return }, coldRecovery)
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
                handler.post { engine = null; sessionOwned = false; engineRestarting = false; bindWorker() }
            } catch (failure: Exception) { handler.post { engineRestarting = false; pendingGuestStart = false; error = failure.message ?: "Guest worker restart failed"; status = "failed" } }
            finally { try { binder.unlinkToDeath(recipient, 0) } catch (_: java.util.NoSuchElementException) { } }
        }, "guest-worker-restart").start()
    }
    override fun pulse(id: Int) = pulse(id, 100)
    private fun pulse(button: Int, holdMs: Long) {
        val worker = engine ?: return
        buttonCalls.execute {
            try { worker.button(button, true); Thread.sleep(holdMs); worker.button(button, false) }
            catch (_: RemoteException) { handler.post { error = "Guest worker unavailable; Start a fresh worker." } }
        }
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
                handler.post { notice = "Screenshot saved to Pictures/LightTouch" }
            } catch (e: Exception) { handler.post { error = e.message ?: "Screenshot failed" } }
        }, "screenshot").start()
    }
    private fun copyAsset(uri: Uri, targetName: String) {
        busy = true
        Thread({
            try {
                val target = File(filesDir, targetName)
                val temporary = File(filesDir, "$targetName.partial")
                contentResolver.openInputStream(uri)?.use { input -> temporary.outputStream().use { input.copyTo(it) } } ?: error("Cannot read file")
                check(temporary.renameTo(target)) { "Could not publish imported file" }
                // Guest tools also replace the selected device's package; the guest
                // installs the newer serial at its next boot.
                val device = if (targetName == "guest.itpack") selected?.id else null
                if (device != null) {
                    val result = engine?.setGuestPackage(device, target.absolutePath) ?: error("Worker unavailable")
                    require(result.isEmpty()) { result }
                }
                handler.post { notice = if (device != null) "Guest tools updated; they install at the next start" else "Imported $targetName"; busy = false }
            } catch (e: Exception) { handler.post { error = e.message ?: "Import failed"; busy = false } }
        }, "asset-import").start()
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
    /** Only the sensors the guest board models are opened; the 2G has an accelerometer and no gyroscope. */
    private fun startSensors() {
        val record = records.firstOrNull { it.getBoolean("selected") }
        if (!settings.tilt || listening || record == null) return
        motionWorker = engine
        displayRotation = (display?.rotation ?: 0) * 90
        val thread = Handler(sensorThread.looper)
        // 100 Hz: the runtime admits up to 200 Hz per stream. Units are already m/s² and rad/s;
        // axis and rotation mapping into guest portrait axes is native (lt_map_motion).
        if (record.getBoolean("acceleration")) sensors.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)?.let { sensors.registerListener(this, it, 10_000, thread) }
        if (record.getBoolean("gyro")) sensors.getDefaultSensor(Sensor.TYPE_GYROSCOPE)?.let { sensors.registerListener(this, it, 10_000, thread) }
        listening = true
    }
    private fun stopSensors() { sensors.unregisterListener(this); listening = false; motionWorker = null }
    override fun onSensorChanged(event: SensorEvent) {
        val kind = if (event.sensor.type == Sensor.TYPE_GYROSCOPE) 2 else 1
        try { motionWorker?.motion(kind, event.timestamp, event.values.copyOf(3), displayRotation) } catch (_: RemoteException) { }
    }
    private fun sendButton(id: Int, down: Boolean) {
        val worker = engine ?: return
        buttonCalls.execute { try { worker.button(id, down) } catch (_: RemoteException) { } }
    }
    /** Full screen hides the system bars; a swipe from the edge shows them briefly. */
    private fun applyWindowMode(immersive: Boolean) {
        if (Build.VERSION.SDK_INT >= 30) window.insetsController?.let { controller ->
            if (immersive) { controller.hide(PlatformInsets.Type.systemBars()); controller.systemBarsBehavior = WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE }
            else controller.show(PlatformInsets.Type.systemBars())
        } else @Suppress("DEPRECATION") {
            window.decorView.systemUiVisibility = if (immersive) (android.view.View.SYSTEM_UI_FLAG_FULLSCREEN or android.view.View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or
                android.view.View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or android.view.View.SYSTEM_UI_FLAG_LAYOUT_STABLE or android.view.View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION or
                android.view.View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN) else (android.view.View.SYSTEM_UI_FLAG_LAYOUT_STABLE or
                android.view.View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION or android.view.View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN)
        }
    }
    private fun recordTarget(): StoreTarget? = records.firstOrNull { it.getBoolean("selected") }?.let { firmwareTargets["${it.getString("board")}-${it.getString("build")}"] }
    private fun openStore() {
        overlay = Screen.STORE
        store.close(); store.dismissJob() // Reopen on the search, not a finished install's details.
        store.useRecord(recordTarget())
        if (muxSocket.isNotEmpty()) store.refreshGuest()
    }
    override fun onAccuracyChanged(sensor: Sensor, accuracy: Int) = Unit
    override fun onStart() { super.onStart(); active = true; DisplayOwners.active.started(this); displayView?.keepScreenOn = status in listOf("starting", "running", "stopping"); handler.post(poll) }
    override fun onResume() {
        super.onResume(); displayResumed = true
        displayOwnerTicket = DisplayOwners.active.resumed(this, hasWindowFocus())
        displayView?.attachActiveSurface()
    }
    override fun onPostResume() {
        super.onPostResume()
        displayOwnerTicket = DisplayOwners.active.focusChanged(this, hasWindowFocus())
        displayView?.attachActiveSurface()
        // Coming back to a guest LightTouch paused on the way out: carry on, once the display is reattached.
        if (pausedByBackground && settings.autoResume) handler.postDelayed({
            if (pausedByBackground && active && displayResumed && status == "paused") setPaused(false)
            pausedByBackground = false
        }, 300)
    }
    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) applyWindowMode(base == Screen.FULLSCREEN && overlay == null) // Transient bars shown by a swipe hide again.
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
        if (wasOwner && ((sessionOwned && status !in listOf("stopping", "stopped", "failed")) || status in listOf("running", "starting"))) {
            pausedByBackground = status == "running" && overlay != Screen.HITCH
            displayView?.backgroundOwnedDisplay()
        }
        else displayView?.retireDisplay()
        DisplayOwners.active.stopped(this, displayOwnerTicket)
        handler.removeCallbacks(poll); monitor.setActive(false, false); super.onStop()
    }
    override fun onDestroy() { buttonCalls.shutdown(); monitor.close(); displayView?.retireDisplay(); DisplayOwners.active.stopped(this, displayOwnerTicket); workerEvidenceDelivery.advance(); handler.removeCallbacksAndMessages(null); stopSensors(); if (bound) unbindService(connection); if (firmwareBound) unbindService(firmwareConnection); super.onDestroy() }
}

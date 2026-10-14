package org.lighttouch.android

import android.content.*
import android.hardware.*
import android.net.Uri
import android.provider.MediaStore
import org.json.JSONObject
import android.os.*
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.*
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.documentfile.provider.DocumentFile
import java.io.File

@OptIn(androidx.compose.ui.ExperimentalComposeUiApi::class)
class MainActivity : ComponentActivity(), SensorEventListener {
    private var engine by mutableStateOf<IEngine?>(null)
    private var status by mutableStateOf("Stopped")
    private var detail by mutableStateOf("")
    private var error by mutableStateOf("")
    private var workerPid by mutableIntStateOf(0)
    private var busy by mutableStateOf(false)
    private var captureNotice by mutableStateOf("")
    private var devices by mutableStateOf<List<Bundle>>(emptyList())
    private var selected by mutableStateOf<Bundle?>(null)
    private var deviceMenu by mutableStateOf(false)
    private var firmware: IFirmware? = null
    private var firmwareBound = false
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
            firmware = IFirmware.Stub.asInterface(binder)
            Thread({
                try {
                    val entries = JSONObject(assets.open("firmware-catalog.json").bufferedReader().use { it.readText() }).getJSONArray("entries")
                    val choices = (0 until entries.length()).map { entries.getJSONObject(it).getString("id") }.map { it to (firmware?.supports(it) == true) }
                    handler.post { firmwareIds = choices; if (firmwareId.isEmpty()) firmwareId = choices.firstOrNull { it.second }?.first ?: "" }
                } catch (e: Exception) { handler.post { error = e.message ?: "Cannot load firmware catalog" } }
            }, "firmware-catalog").start()
        }
        override fun onServiceDisconnected(name: ComponentName) { firmware = null; preparation = Bundle().apply { putString("state", "failed"); putString("error", "Firmware worker exited. Interrupted staging is preserved and cannot be imported.") } }
    }
    private val stockIpsw = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> if (uri != null) importStockIpsw(uri) }
    private val trustedTools = registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri -> if (uri != null) importTrustedTools(uri) }
    private var active = false
    private var bound = false
    private var displayView: GuestDisplay? = null
    private val handler = Handler(Looper.getMainLooper())
    private val sensors by lazy { getSystemService(SensorManager::class.java) }
    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName, binder: IBinder) {
            engine = IEngine.Stub.asInterface(binder)
            refreshDevices()
            binder.linkToDeath({ handler.post { engine = null; status = "Worker exited"; workerPid = 0; stopSensors() } }, 0)
            displayView?.holder?.surface?.takeIf { it.isValid }?.let { engine?.surface(it) }
        }
        override fun onServiceDisconnected(name: ComponentName) { engine = null; workerPid = 0; status = "Worker exited"; stopSensors() }
    }
    private val poll = object : Runnable {
        override fun run() {
            try {
                if (preparationOpen) pollPreparation()
                engine?.status()?.let { b ->
                    status = b.getString("state") ?: "Unknown"
                    detail = b.getString("detail") ?: ""
                    val reportedError = b.getString("error") ?: ""
                    if (reportedError.isNotEmpty() || status == "running") error = reportedError
                    workerPid = b.getInt("pid")
                    if (active && status == "running") startSensors() else stopSensors()
                }
            } catch (e: RemoteException) { engine = null; error = "Worker unavailable: ${e.message}"; stopSensors() }
            handler.postDelayed(this, 500)
        }
    }
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
        val imports = getSharedPreferences("preparation-imports", MODE_PRIVATE)
        ipswPath = imports.getString("ipsw", "") ?: ""; toolsPath = imports.getString("tools", "") ?: ""
        ipswSelected = File(ipswPath).isFile; toolsSelected = File(toolsPath).isDirectory
        firmwareId = imports.getString("firmwareId", "") ?: ""
        preparedImported = imports.getString("preparedImported", "") ?: ""
        bindWorker()
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(primary = Color(0xFF71DCC8))) {
                Surface(Modifier.fillMaxSize()) {
                    Column(Modifier.fillMaxSize().padding(16.dp).semantics { testTagsAsResourceId = true }) {
                        Text("LightTouch", style = MaterialTheme.typography.headlineMedium)
                        Box {
                            OutlinedButton(onClick = { refreshDevices(); deviceMenu = true }, enabled = !busy && status !in listOf("running", "paused", "starting", "stopping"), modifier = Modifier.testTag("device_selector")) {
                                Text(selected?.let { "${it.getString("name")} · ${it.getString("board")} ${it.getString("build")}" } ?: "Select or import device")
                            }
                            DropdownMenu(expanded = deviceMenu, onDismissRequest = { deviceMenu = false }) {
                                for (device in devices) DropdownMenuItem(text = { Text("${device.getString("name")} · ${device.getString("board")} ${device.getString("build")}") }, onClick = {
                                    val result = engine?.select(device.getString("id")) ?: "Worker unavailable"
                                    if (result.isNotEmpty()) error = result
                                    refreshDevices(); deviceMenu = false
                                })
                            }
                        }
                        selected?.let { record -> Text("Guest contacts ${record.getString("contacts")}; gyro ${if (record.getBoolean("gyro")) "deferred/unqualified (modeled)" else "deferred/unqualified (not modeled)"}; ${if (record.getBoolean("dirty")) "state dirty" else "state clean"}", style = MaterialTheme.typography.bodySmall) }
                        Text(status, Modifier.testTag("session_status"), style = MaterialTheme.typography.titleMedium)
                        Text(detail, style = MaterialTheme.typography.bodySmall)
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
                            Button(onClick = { startGuest() }, enabled = !busy && selected != null && status !in listOf("running", "starting", "stopping", "paused"), modifier = Modifier.testTag("start_guest")) { Text("Start") }
                            Button(onClick = { engine?.stop(); stopSensors(); status = "stopping" }, enabled = engine != null && status != "stopping", modifier = Modifier.testTag("stop_guest")) { Text("Stop") }
                            Button(onClick = { engine?.pause(status != "paused") }, enabled = status in listOf("running", "paused")) { Text(if (status == "paused") "Resume" else "Pause") }
                        }
                        AndroidView(factory = { GuestDisplay(it) { engine }.also { displayView = it } }, modifier = Modifier.weight(1f).fillMaxWidth().background(Color.Black).testTag("guest_display"))
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = { pulse(0) }, enabled = status == "running", modifier = Modifier.testTag("home_button")) { Text("Home") }
                            Button(onClick = { pulse(1) }, enabled = status == "running", modifier = Modifier.testTag("power_button")) { Text("Power") }
                            Button(onClick = { capture() }, enabled = status in listOf("running", "paused"), modifier = Modifier.testTag("screenshot_button")) { Text("Screenshot") }
                        }
                        Text("Backgrounding pauses the guest. Reopen and tap Resume.", style = MaterialTheme.typography.bodySmall)
                        if (status !in listOf("running", "paused", "starting", "stopping")) Text("Host gyro: ${sensors.getDefaultSensor(Sensor.TYPE_GYROSCOPE)?.name ?: "Unavailable"}. Guest gyro deferred/unqualified.", style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
            if (preparationOpen) PreparationDialog()
        }
    }
    @Composable
    private fun PreparationDialog() {
        AlertDialog(onDismissRequest = { if (preparation?.getString("state") != "preparing") preparationOpen = false },
            title = { Text("Prepare firmware") },
            text = {
                Column {
                    Text("Choose a legitimate stock IPSW and the verified guest tools folder. Preparation runs locally without root.")
                    Box {
                        OutlinedButton(onClick = { firmwareMenu = true }, enabled = preparation?.getString("state") != "preparing") { Text(firmwareId.ifEmpty { "Choose firmware" }) }
                        DropdownMenu(expanded = firmwareMenu, onDismissRequest = { firmwareMenu = false }) {
                            for ((id, supported) in firmwareIds) DropdownMenuItem(text = { Text(if (supported) id else "$id · unsupported route") }, onClick = { firmwareId = id; getSharedPreferences("preparation-imports", MODE_PRIVATE).edit().putString("firmwareId", id).apply(); firmwareMenu = false })
                        }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = { stockIpsw.launch(arrayOf("application/octet-stream", "*/*")) }, enabled = !busy) { Text("Stock IPSW") }
                        Button(onClick = { trustedTools.launch(null) }, enabled = !busy) { Text("Verified tools") }
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
                else if (preparation?.getString("state") == "preparing") TextButton(onClick = { firmware?.cancel() }, modifier = Modifier.testTag("cancel_preparation")) { Text("Cancel") }
                else TextButton(onClick = { beginPreparation() }, enabled = !busy && ipswSelected && toolsSelected && firmwareIds.any { it.first == firmwareId && it.second }, modifier = Modifier.testTag("start_preparation")) { Text("Prepare") }
            },
            dismissButton = { TextButton(onClick = { if (preparation?.getString("state") != "preparing") { firmware?.finish(); preparationOpen = false } }, enabled = preparation?.getString("state") != "preparing") { Text("Close") } })
    }
    private fun bindFirmware() {
        if (firmwareBound) { unbindService(firmwareConnection); firmwareBound = false }
        firmwareBound = bindService(Intent(this, FirmwareService::class.java), firmwareConnection, BIND_AUTO_CREATE)
    }
    private fun beginPreparation() {
        if (preparation?.getString("state") in listOf("failed", "cancelled", "complete")) {
            firmware?.finish(); preparation = null; firmware = null
            handler.postDelayed({ bindFirmware(); handler.postDelayed({ beginPreparation() }, 500) }, 500)
            return
        }
        if (firmware == null) { error = "Firmware worker unavailable"; return }
        startForegroundService(Intent(this, FirmwareService::class.java))
        Thread({ try { val state = firmware!!.start(firmwareId, ipswPath, toolsPath); handler.post { preparation = state } }
            catch (e: Exception) { handler.post { error = e.message ?: "Cannot start preparation" } } }, "prepare-start").start()
    }
    private fun pollPreparation() {
        val state = firmware?.status() ?: return
        preparation = state
        state.getString("firmwareId")?.takeIf { it.isNotEmpty() }?.let { firmwareId = it }
        if (state.getString("state") == "complete" && state.getString("destination") != preparedImported) importPreparedOutput(state)
    }
    private fun importPreparedOutput(state: Bundle) {
        val destination = requireNotNull(state.getString("destination")); preparedImported = destination; busy = true
        Thread({
            val result = try { engine?.importPrepared(destination, "$firmwareId prepared") ?: "Device worker unavailable" }
                catch (e: RemoteException) { e.message ?: "Device worker exited during import" }
            handler.post {
                busy = false; error = result; preparedImportError = result
                if (result.isEmpty()) {
                    getSharedPreferences("preparation-imports", MODE_PRIVATE).edit().putString("preparedImported", destination).apply()
                    detail = "Prepared firmware imported as a new device"; refreshDevices(); firmware?.finish()
                }
            }
        }, "prepared-device-import").start()
    }
    private fun importStockIpsw(uri: Uri) {
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
        if (bound) { unbindService(connection); bound = false }
        bound = bindService(Intent(this, EngineService::class.java), connection, BIND_AUTO_CREATE)
    }
    private fun startGuest() {
        if (engine == null) { bindWorker(); handler.postDelayed({ if (engine != null) startGuest() else error = "Worker could not connect" }, 500); return }
        startForegroundService(Intent(this, EngineService::class.java))
        Thread({
            try { val id = selected?.getString("id") ?: error("Select a device")
                val b = engine!!.start(id); handler.post { error = b.getString("error") ?: "" } }
            catch (e: Exception) { handler.post { error = e.message ?: "Start failed" } }
        }, "guest-start").start()
    }
    private fun pulse(button: Int) { engine?.button(button, true); handler.postDelayed({ engine?.button(button, false) }, 100) }
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
    // User scope D12: preserve adapters, keep automatic experimental motion off.
    private val experimentalMotionEnabled = false
    private var listening = false
    private fun startSensors() {
        if (!experimentalMotionEnabled || listening) return
        // Android units are already rad/s and m/s². Axis transformation is native.
        sensors.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)?.let { sensors.registerListener(this, it, 20_000) }
        sensors.getDefaultSensor(Sensor.TYPE_GYROSCOPE)?.let { sensors.registerListener(this, it, 20_000) }
        listening = true
    }
    private fun stopSensors() { sensors.unregisterListener(this); listening = false }
    override fun onSensorChanged(event: SensorEvent) {
        val kind = if (event.sensor.type == Sensor.TYPE_GYROSCOPE) 2 else 1
        engine?.motion(kind, event.timestamp, event.values.copyOf(3), display?.rotation?.times(90) ?: 0)
    }
    override fun onAccuracyChanged(sensor: Sensor, accuracy: Int) = Unit
    override fun onStart() { super.onStart(); active = true; handler.post(poll) }
    override fun onStop() { active = false; stopSensors(); engine?.cancelInput(); if (status == "running") engine?.pause(true); handler.removeCallbacks(poll); super.onStop() }
    override fun onDestroy() { handler.removeCallbacksAndMessages(null); stopSensors(); if (bound) unbindService(connection); if (firmwareBound) unbindService(firmwareConnection); super.onDestroy() }
}

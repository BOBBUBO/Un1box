package org.lighttouch.android

import android.content.*
import android.hardware.*
import android.net.Uri
import android.provider.MediaStore
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
    private var active = false
    private var bound = false
    private var displayView: GuestDisplay? = null
    private val handler = Handler(Looper.getMainLooper())
    private val sensors by lazy { getSystemService(SensorManager::class.java) }
    private val base by lazy { File(filesDir, "base") }
    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName, binder: IBinder) {
            engine = IEngine.Stub.asInterface(binder)
            binder.linkToDeath({ handler.post { engine = null; status = "Worker exited"; workerPid = 0; stopSensors() } }, 0)
            displayView?.holder?.surface?.takeIf { it.isValid }?.let { engine?.surface(it) }
        }
        override fun onServiceDisconnected(name: ComponentName) { engine = null; workerPid = 0; status = "Worker exited"; stopSensors() }
    }
    private val poll = object : Runnable {
        override fun run() {
            try {
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
        bindWorker()
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(primary = Color(0xFF71DCC8))) {
                Surface(Modifier.fillMaxSize()) {
                    Column(Modifier.fillMaxSize().padding(16.dp).semantics { testTagsAsResourceId = true }) {
                        Text("LightTouch", style = MaterialTheme.typography.headlineMedium)
                        Text(status, Modifier.testTag("session_status"), style = MaterialTheme.typography.titleMedium)
                        Text(detail, style = MaterialTheme.typography.bodySmall)
                        if (captureNotice.isNotEmpty()) Text(captureNotice, Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                        if (error.isNotEmpty()) Text(error, Modifier.testTag("session_error").semantics { liveRegion = LiveRegionMode.Polite }, color = MaterialTheme.colorScheme.error)
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = { importArchive.launch(arrayOf("application/octet-stream", "*/*")) }, enabled = !busy && status !in listOf("running", "paused", "starting", "stopping"), contentPadding = PaddingValues(horizontal = 12.dp), modifier = Modifier.testTag("import_base")) { Text("Import base") }
                            Button(onClick = { assetName = "bootrom.bin"; importFile.launch(arrayOf("application/octet-stream", "*/*")) }, enabled = !busy, contentPadding = PaddingValues(horizontal = 12.dp), modifier = Modifier.semantics { contentDescription = "Import optional ROM" }) { Text("ROM") }
                            Button(onClick = { assetName = "guest.itpack"; importFile.launch(arrayOf("application/octet-stream", "*/*")) }, enabled = !busy, contentPadding = PaddingValues(horizontal = 12.dp)) { Text("Guest tools") }
                        }
                        TextButton(onClick = { importTree.launch(null) }, enabled = !busy && !base.exists()) { Text("Import prepared folder instead") }
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = { startGuest() }, enabled = !busy && base.isDirectory && status !in listOf("running", "starting", "stopping", "paused"), modifier = Modifier.testTag("start_guest")) { Text("Start") }
                            Button(onClick = { engine?.stop(); stopSensors(); status = "stopping" }, enabled = engine != null && status != "stopping", modifier = Modifier.testTag("stop_guest")) { Text("Stop") }
                            Button(onClick = { engine?.pause(status != "paused") }, enabled = status in listOf("running", "paused")) { Text(if (status == "paused") "Resume" else "Pause") }
                        }
                        AndroidView(factory = { GuestDisplay(it) { engine }.also { displayView = it } }, modifier = Modifier.weight(1f).fillMaxWidth().background(Color.Black).testTag("guest_display"))
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = { pulse(0) }, enabled = status == "running", modifier = Modifier.testTag("home_button")) { Text("Home") }
                            Button(onClick = { pulse(1) }, enabled = status == "running", modifier = Modifier.testTag("power_button")) { Text("Power") }
                            Button(onClick = { capture() }, enabled = status in listOf("running", "paused"), modifier = Modifier.testTag("screenshot_button")) { Text("Screenshot") }
                        }
                        Text("Backgrounding pauses the guest. Reopen and tap Resume. Worker $workerPid", style = MaterialTheme.typography.bodySmall)
                        Text("Host gyro: ${sensors.getDefaultSensor(Sensor.TYPE_GYROSCOPE)?.name ?: "Unavailable"}. Guest support is reported above.", style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }
    }
    private fun bindWorker() {
        if (bound) { unbindService(connection); bound = false }
        bound = bindService(Intent(this, EngineService::class.java), connection, BIND_AUTO_CREATE)
    }
    private fun startGuest() {
        if (engine == null) { bindWorker(); handler.postDelayed({ if (engine != null) startGuest() else error = "Worker could not connect" }, 500); return }
        startForegroundService(Intent(this, EngineService::class.java))
        Thread({
            try { val b = engine!!.start(base.absolutePath); handler.post { error = b.getString("error") ?: "" } }
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
                require(!base.exists()) { "A base is already imported; existing state is preserved" }
                contentResolver.openInputStream(uri)?.use { input -> staging.outputStream().use { input.copyTo(it) } } ?: error("Cannot read selected base")
                val result = engine?.importBase(staging.absolutePath, base.absolutePath) ?: error("Worker unavailable")
                require(result.isEmpty()) { result }
                handler.post { detail = "Prepared base validated and imported"; busy = false }
            } catch (e: Exception) { handler.post { error = e.message ?: "Import failed"; busy = false } }
            finally { staging.delete() }
        }, "base-archive-import").start()
    }
    private fun importDirectory(uri: Uri) {
        busy = true
        Thread({
            val staging = File(filesDir, "import-${System.nanoTime()}")
            try {
                require(!base.exists()) { "A base is already imported; existing state is preserved" }
                val source = DocumentFile.fromTreeUri(this, uri) ?: error("Cannot read selected directory")
                copyTree(source, staging)
                val result = engine?.importBase(staging.absolutePath, base.absolutePath) ?: error("Worker unavailable")
                require(result.isEmpty()) { result }
                handler.post { detail = "Prepared base validated and imported"; busy = false }
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
    private fun startSensors() {
        if (listening) return
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
    override fun onDestroy() { handler.removeCallbacksAndMessages(null); stopSensors(); if (bound) unbindService(connection); super.onDestroy() }
}

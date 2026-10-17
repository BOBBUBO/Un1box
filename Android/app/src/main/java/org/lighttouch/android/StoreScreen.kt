package org.lighttouch.android

import android.content.Context
import android.graphics.Bitmap
import android.net.Uri
import android.os.Handler
import android.os.Looper
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.util.Locale

/** A JSON string field, or null when absent, empty or JSON null (optString would return "null"). */
internal fun JSONObject.text(key: String): String? = if (isNull(key)) null else optString(key).ifEmpty { null }

internal fun productName(productType: String): String = when (productType) {
    "iPod1,1" -> "iPod touch"; "iPod2,1" -> "iPod touch (2nd generation)"; "iPod3,1" -> "iPod touch (3rd generation)"
    "iPod4,1" -> "iPod touch (4th generation)"; "iPhone1,1" -> "iPhone"; "iPhone1,2" -> "iPhone 3G"; "iPhone2,1" -> "iPhone 3GS"
    "iPhone3,1" -> "iPhone 4"; "iPad1,1" -> "iPad"; else -> productType
}

internal fun byteSize(bytes: Long): String = when {
    bytes >= 1L shl 30 -> String.format(Locale.ROOT, "%.1f GB", bytes / 1073741824.0)
    bytes >= 1L shl 20 -> String.format(Locale.ROOT, "%.1f MB", bytes / 1048576.0)
    else -> String.format(Locale.ROOT, "%d KB", bytes / 1024)
}

/** One install, from download to the guest confirming the bundle in its app list. */
internal data class InstallJob(val name: String, val phase: String, val progress: Float? = null, val error: String? = null,
                               val done: Boolean = false, val cancellable: Boolean = false)

/**
 * Legacy Store state. Network and guest-service work runs on plain worker threads and posts
 * back to the main thread, like the rest of the activity. [muxSocket] is the current worker's
 * usbmux socket, or null while no guest is running.
 */
internal class StoreModel(private val context: Context, private val muxSocket: () -> String?) {
    private val handler = Handler(Looper.getMainLooper())
    private val apps = GuestApps(context)
    var target by mutableStateOf<StoreTarget?>(null)
    var targetConfirmed by mutableStateOf(false)
    var query by mutableStateOf("")
    var results by mutableStateOf<List<JSONObject>>(emptyList())
    var showIncompatible by mutableStateOf(true)
    var searching by mutableStateOf(false)
    var message by mutableStateOf("")
    var chosen by mutableStateOf<JSONObject?>(null)
    var copies by mutableStateOf<List<StoreCopy>?>(null)
    var selected by mutableStateOf<Int?>(null)
    var problem by mutableStateOf<String?>(null)
    var candidate by mutableStateOf<JSONObject?>(null)
    var installed by mutableStateOf<Map<String, String>>(emptyMap())
    var job by mutableStateOf<InstallJob?>(null)
    var crash by mutableStateOf<String?>(null)
    @Volatile private var cancelDownload = false
    private var searchGeneration = 0
    private var checkGeneration = 0

    val servicesAvailable get() = apps.available
    val installing get() = job?.let { !it.done && it.error == null } == true

    private fun background(name: String, work: () -> Unit) = Thread(work, name).start()
    private fun post(work: () -> Unit) = handler.post(work)

    /** Target from the selected device record, until a running guest reports its own. */
    fun useRecord(next: StoreTarget?) {
        if (next == target || (targetConfirmed && next?.productType == target?.productType)) return
        target = next; targetConfirmed = false; results = emptyList(); chosen = null
        if (next != null) search()
    }

    /** Ask the running guest for its exact model and iOS version, and which apps it already has. */
    fun refreshGuest() {
        val socket = muxSocket() ?: return
        if (!apps.available) return
        background("store-guest") {
            try {
                val exact = apps.target(socket)
                val list = apps.installed(socket)
                post {
                    installed = list
                    if (exact != target) { target = exact; targetConfirmed = true; search() } else targetConfirmed = true
                }
            } catch (e: Exception) { post { if (!installing) message = "Guest app services: ${e.message}" } }
        }
    }

    fun search() {
        val t = target ?: run { message = "Select a device first."; return }
        val q = query; val generation = ++searchGeneration
        searching = true; message = ""
        background("store-search") {
            val outcome = runCatching { LegacyStore.search(q, t) }
            post {
                if (generation != searchGeneration) return@post
                searching = false
                outcome.onSuccess { results = it; if (it.isEmpty()) message = "No apps found." }
                    .onFailure { message = it.message ?: "Search failed" }
            }
        }
    }

    fun open(app: JSONObject) {
        val t = target ?: return
        chosen = app; copies = null; selected = null; problem = null; candidate = null
        val generation = ++checkGeneration
        background("store-versions") {
            val outcome = runCatching { LegacyStore.versions(app, t) }
            post {
                if (generation != checkGeneration) return@post
                outcome.onSuccess { rows ->
                    copies = rows
                    if (rows.isEmpty()) problem = "No version of this app runs on this device."
                    else choose(rows.firstOrNull { it.ipaId == app.optInt("ipa_id") }?.ipaId ?: rows[0].ipaId)
                }.onFailure { copies = emptyList(); problem = "Couldn't load versions: ${it.message}" }
            }
        }
    }

    fun close() { chosen = null; checkGeneration++ }

    /** Revalidate one archived copy against this device (CatalogDetailsModel.check). */
    fun choose(ipaId: Int) {
        val t = target ?: return
        val app = chosen ?: return
        selected = ipaId; problem = null; candidate = null
        val generation = ++checkGeneration
        background("store-copy-check") {
            val outcome = runCatching {
                val found = LegacyStore.compatibleCopy(ipaId, t)
                if (found.optString("bundle_id") != app.optString("bundle_id")) throw StoreError("This copy belongs to a different app.")
                LegacyStore.checkCopy(LegacyStore.copyDetails(ipaId), found, t)
                found
            }
            post {
                if (generation != checkGeneration) return@post
                outcome.onSuccess { candidate = it }.onFailure { problem = it.message }
            }
        }
    }

    fun install(app: JSONObject) = runInstall(app.optString("name")) { report ->
        val socket = muxSocket() ?: throw StoreError("Start the guest to install apps.")
        val exact = apps.target(socket)
        cancelDownload = false
        report(InstallJob(app.optString("name"), "Downloading", 0f, cancellable = true))
        val ipa = LegacyStore.download(app, exact, File(context.cacheDir, "store"), { count, total ->
            report(InstallJob(app.optString("name"), "Downloading " + byteSize(count) + (total?.let { " of " + byteSize(it) } ?: ""),
                total?.let { count.toFloat() / it }, cancellable = true))
        }) { cancelDownload }
        try { installPrepared(socket, exact, ipa, app.optString("name"), report) } finally { ipa.parentFile?.deleteRecursively() }
    }

    fun installLocal(uri: Uri) = runInstall("Local IPA") { report ->
        val socket = muxSocket() ?: throw StoreError("Start the guest to install apps.")
        val exact = apps.target(socket)
        val directory = File(context.cacheDir, "store/local-${System.nanoTime()}").apply { mkdirs() }
        try {
            report(InstallJob("Local IPA", "Copying"))
            val copy = File(directory, "Source.ipa")
            context.contentResolver.openInputStream(uri)?.use { input -> copy.outputStream().use { input.copyTo(it) } } ?: throw StoreError("Cannot read the selected file.")
            installPrepared(socket, exact, copy, null, report)
        } finally { directory.deleteRecursively() }
    }

    private fun installPrepared(socket: String, target: StoreTarget, ipa: File, label: String?, report: (InstallJob) -> Unit) {
        report(InstallJob(label ?: "Local IPA", "Checking app"))
        val prepared = File(ipa.parentFile, "Install.ipa")
        val info = IpaPreflight.prepare(ipa, prepared, target)
        val name = label ?: info.name
        apps.install(socket, prepared, info.bundleId) { e ->
            when (e.optString("phase")) {
                "upload" -> report(InstallJob(name, "Copying to device", e.optInt("percent") / 100f))
                "mutation" -> report(InstallJob(name, "Installing"))
                "install" -> report(InstallJob(name, "Installing · " + e.optString("status", ""),
                    if (e.has("percent") && !e.isNull("percent")) e.optInt("percent") / 100f else null))
            }
        }
        val list = runCatching { apps.installed(socket) }.getOrNull()
        post { list?.let { installed = it } }
        report(InstallJob(name, "Installed. Find it on the Home screen.", 1f, done = true))
    }

    private fun runInstall(name: String, work: ((InstallJob) -> Unit) -> Unit) {
        if (installing) return
        job = InstallJob(name, "Preparing")
        background("store-install") {
            val report: (InstallJob) -> Unit = { next -> post { job = next } }
            try { work(report) } catch (e: Exception) {
                post { job = (job ?: InstallJob(name, "")).copy(error = e.message ?: "Install failed", cancellable = false) }
            }
        }
    }

    /** Why the last app quit: the useful lines of the guest's newest crash report. */
    fun showLastCrash() {
        val socket = muxSocket() ?: run { crash = "Start the guest to read its crash reports."; return }
        crash = "Reading the guest's crash reports…"
        background("store-crash") {
            val text = try {
                apps.lastCrash(socket)?.let { (name, body) ->
                    val keep = Regex("^(Process|Date/Time|OS Version|Exception Type|Exception Codes|Crashed Thread|Dyld Error Message|  Symbol not found|  Referenced from|  Expected in|Termination Reason|Application Specific Information)")
                    val lines = body.lines()
                    val dyld = lines.indexOfFirst { it.startsWith("Dyld Error Message") }
                    name + "\n\n" + lines.filterIndexed { i, l -> keep.containsMatchIn(l) || (dyld >= 0 && i in dyld + 1..dyld + 3) }
                        .distinct().joinToString("\n").ifEmpty { body.take(1500) }
                } ?: "The guest has no crash reports."
            } catch (e: Exception) { "Could not read crash reports: ${e.message}" }
            post { crash = text }
        }
    }

    fun cancel() { cancelDownload = true }
    fun dismissJob() { if (!installing) job = null }
}

@Composable
private fun AppIcon(url: String?, size: Int = 48) {
    val bitmap by produceState<Bitmap?>(null, url) { value = url?.let { withContext(Dispatchers.IO) { LegacyStore.icon(it) } } }
    Box(Modifier.size(size.dp).clip(RoundedCornerShape((size / 5).dp)).background(Color(0xFF2C2C2E))) {
        bitmap?.let { Image(it.asImageBitmap(), contentDescription = null, Modifier.fillMaxSize()) }
    }
}

/** Full-screen Legacy Store: search, compatibility, version picker and install progress. */
@Composable
internal fun StoreScreen(model: StoreModel, running: Boolean, onPickLocal: () -> Unit, onClose: () -> Unit) {
    BackHandler(enabled = model.chosen == null, onBack = onClose)
    // Drawn over the activity's content (not a Dialog window, which gets no usable insets edge to edge).
    Surface(Modifier.fillMaxSize().testTag("store")) {
            Column(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing).padding(horizontal = 16.dp, vertical = 8.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("Legacy Store", style = MaterialTheme.typography.headlineSmall)
                        Text(model.target?.let { "${productName(it.productType)} · iOS ${it.ios}" + if (model.targetConfirmed) "" else " (from device record)" } ?: "No device selected",
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    TextButton(onClick = { model.showLastCrash() }, enabled = running && model.servicesAvailable, modifier = Modifier.testTag("store_crash")) { Text("Last crash") }
                    TextButton(onClick = onClose, modifier = Modifier.testTag("store_close")) { Text("Done") }
                }
                model.crash?.let { report ->
                    Card(Modifier.fillMaxWidth().padding(vertical = 8.dp).testTag("store_crash_report")) {
                        Column(Modifier.padding(12.dp)) {
                            Text(report, style = MaterialTheme.typography.bodySmall.copy(fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace),
                                modifier = Modifier.heightIn(max = 260.dp).verticalScroll(rememberScrollState()))
                            Row { Spacer(Modifier.weight(1f)); TextButton(onClick = { model.crash = null }) { Text("Close") } }
                        }
                    }
                }
                model.job?.let { InstallCard(it, model) }
                val details = model.chosen
                if (details != null) {
                    BackHandler { model.close() }
                    StoreDetails(model, details, running)
                } else {
                    val keyboard = LocalSoftwareKeyboardController.current
                    val search = { keyboard?.hide(); model.search() }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        OutlinedTextField(model.query, { model.query = it }, Modifier.weight(1f).testTag("store_query"), singleLine = true,
                            placeholder = { Text("Search apps and games") },
                            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search), keyboardActions = KeyboardActions(onSearch = { search() }))
                        Spacer(Modifier.width(8.dp))
                        Button(onClick = { search() }, enabled = !model.searching && model.target != null, modifier = Modifier.testTag("store_search")) { Text("Search") }
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(model.showIncompatible, { model.showIncompatible = it })
                        Text("Show apps that won't run", style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
                        TextButton(onClick = onPickLocal, enabled = running && !model.installing && model.servicesAvailable) { Text("Install IPA file…") }
                    }
                    if (model.searching) LinearProgressIndicator(Modifier.fillMaxWidth())
                    if (model.message.isNotEmpty()) Text(model.message, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                    val rows = if (model.showIncompatible) model.results else model.results.filter { LegacyStore.incompatibility(it) == null }
                    LazyColumn(Modifier.weight(1f).testTag("store_results")) {
                        items(rows, key = { it.optInt("ipa_id") }) { app -> ResultRow(app, model) }
                    }
                }
            }
        }
}

@Composable
private fun ResultRow(app: JSONObject, model: StoreModel) {
    val problem = LegacyStore.incompatibility(app)
    val installedVersion = model.installed[app.optString("bundle_id")]
    Row(Modifier.fillMaxWidth().clickable { model.open(app) }.padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        AppIcon(app.optString("icon_url").ifEmpty { null })
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(app.optString("name"), maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleSmall,
                color = if (problem == null) Color.Unspecified else MaterialTheme.colorScheme.onSurfaceVariant)
            Text(listOfNotNull(app.text("developer"), app.text("version"),
                app.optLong("size").takeIf { it > 0 }?.let(::byteSize)).joinToString(" · "),
                maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            when {
                problem != null -> Text(problem, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                installedVersion != null -> Text("Installed $installedVersion", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary)
            }
        }
    }
}

@Composable
private fun StoreDetails(model: StoreModel, app: JSONObject, running: Boolean) {
    Column(Modifier.fillMaxSize()) {
        TextButton(onClick = { model.close() }) { Text("‹ Results") }
        Row(verticalAlignment = Alignment.CenterVertically) {
            AppIcon(app.optString("icon_url").ifEmpty { null }, 64)
            Spacer(Modifier.width(12.dp))
            Column {
                Text(app.optString("name"), style = MaterialTheme.typography.titleMedium)
                Text(app.text("developer") ?: "", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                model.installed[app.optString("bundle_id")]?.let { Text("Installed $it", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary) }
            }
        }
        Spacer(Modifier.height(12.dp))
        Text("Versions that run on this device", style = MaterialTheme.typography.labelLarge)
        val rows = model.copies
        if (rows == null) LinearProgressIndicator(Modifier.fillMaxWidth().padding(vertical = 8.dp))
        LazyColumn(Modifier.weight(1f).testTag("store_versions")) {
            items(rows ?: emptyList(), key = { it.ipaId }) { copy ->
                val twin = (rows ?: emptyList()).count { it.version == copy.version } > 1
                Row(Modifier.fillMaxWidth().clickable { model.choose(copy.ipaId) }.padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    RadioButton(model.selected == copy.ipaId, { model.choose(copy.ipaId) })
                    Column {
                        Text(copy.version + if (twin) " · copy ${copy.ipaId}" else "")
                        Text(listOfNotNull(copy.size?.let(::byteSize), copy.minimumOS?.let { "iOS $it or later" }).joinToString(" · "),
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
        model.problem?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
        val installedVersion = model.installed[app.optString("bundle_id")]
        val chosenVersion = rows?.firstOrNull { it.ipaId == model.selected }?.version
        if (installedVersion != null && chosenVersion != null) {
            val a = LegacyStore.version(chosenVersion.substringBefore(' ')); val b = LegacyStore.version(installedVersion)
            if (a != null && b != null && LegacyStore.newer(b, a)) Text("Version $installedVersion is installed. An older version may not read its data.",
                style = MaterialTheme.typography.bodySmall)
        }
        if (!running) Text("Start the guest to install.", style = MaterialTheme.typography.bodySmall)
        else if (!model.servicesAvailable) Text("App installation is not included in this build.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
        Button(onClick = { model.candidate?.let { model.install(it) } }, Modifier.fillMaxWidth().testTag("store_install"),
            enabled = running && model.servicesAvailable && model.candidate != null && !model.installing) {
            Text(if (installedVersion != null) "Reinstall" else "Install")
        }
    }
}

@Composable
private fun InstallCard(job: InstallJob, model: StoreModel) {
    Card(Modifier.fillMaxWidth().padding(vertical = 8.dp).testTag("store_job")) {
        Column(Modifier.padding(12.dp)) {
            Text(job.name, style = MaterialTheme.typography.titleSmall)
            Text(job.error ?: job.phase, style = MaterialTheme.typography.bodySmall,
                color = if (job.error != null) MaterialTheme.colorScheme.error else Color.Unspecified)
            if (job.error == null && !job.done) {
                if (job.progress != null) LinearProgressIndicator(progress = { job.progress.coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth().padding(top = 6.dp))
                else LinearProgressIndicator(Modifier.fillMaxWidth().padding(top = 6.dp))
            }
            Row {
                Spacer(Modifier.weight(1f))
                if (job.cancellable) TextButton(onClick = { model.cancel() }) { Text("Cancel") }
                if (job.done || job.error != null) TextButton(onClick = { model.dismissJob() }) { Text("OK") }
            }
        }
    }
}

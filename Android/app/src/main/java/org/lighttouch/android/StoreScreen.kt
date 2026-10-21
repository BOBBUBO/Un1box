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
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Search
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.testTagsAsResourceId
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

    /** The device record the store is for: the installed apps and the confirmed target are that device's alone. */
    private var recordId = ""

    /**
     * Target from the selected device record, until a running guest reports its own. Another record starts over,
     * even of the same model: two iPod touch 2Gs on different iOS versions have different apps.
     */
    fun useRecord(id: String, next: StoreTarget?) {
        if (id != recordId) { recordId = id; installed = emptyMap(); targetConfirmed = false; target = null; results = emptyList(); chosen = null }
        if (next == target || (targetConfirmed && next?.productType == target?.productType)) return
        target = next; targetConfirmed = false; results = emptyList(); chosen = null
        if (next != null) search()
    }

    /** Another guest (or none) is behind the usbmux socket: what the last one reported no longer holds. */
    fun guestChanged() { installed = emptyMap(); targetConfirmed = false }

    /** Ask the running guest for its exact model and iOS version, and which apps it already has. */
    fun refreshGuest() {
        val socket = muxSocket() ?: return
        if (!apps.available) return
        background("store-guest") {
            try {
                val exact = apps.target(socket)
                val list = apps.installed(socket)
                post {
                    if (muxSocket() != socket) return@post  // a different guest answered by now
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
                    else choose(rows.firstOrNull { it.ipaId == LegacyStore.preferred(app.optInt("ipa_id")) }?.ipaId ?: rows[0].ipaId)
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
    Box(Modifier.size(size.dp).clip(RoundedCornerShape((size / 4.5f).dp)).background(MaterialTheme.colorScheme.surfaceContainerHighest)) {
        bitmap?.let { Image(it.asImageBitmap(), contentDescription = null, Modifier.fillMaxSize()) }
    }
}

/** Full-screen Legacy Store: search, compatibility, version picker and install progress. */
@OptIn(androidx.compose.ui.ExperimentalComposeUiApi::class)
@Composable
internal fun StoreScreen(model: StoreModel, running: Boolean, onPickLocal: () -> Unit, onClose: () -> Unit) {
    BackHandler(enabled = model.chosen == null, onBack = onClose)
    val details = model.chosen
    // Drawn over the activity's content (not a Dialog window, which gets no usable insets edge to edge).
    Surface(Modifier.fillMaxSize().testTag("store").semantics { testTagsAsResourceId = true }, color = MaterialTheme.colorScheme.background) {
        Column(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing)) {
            StoreTopBar(model, running, onPickLocal, onBack = if (details != null) model::close else onClose)
            Column(Modifier.padding(horizontal = 16.dp)) {
                model.crash?.let { report ->
                    StoreCard(Modifier.testTag("store_crash_report")) {
                        Text("Last crash", style = MaterialTheme.typography.titleSmall)
                        Spacer(Modifier.height(6.dp))
                        Text(report, style = MonoStyle, modifier = Modifier.heightIn(max = 260.dp).verticalScroll(rememberScrollState()))
                        Row { Spacer(Modifier.weight(1f)); TextButton(onClick = { model.crash = null }) { Text("Close") } }
                    }
                }
                model.job?.let { InstallCard(it, model) }
            }
            if (details != null) {
                BackHandler { model.close() }
                StoreDetails(model, details, running)
            } else {
                SearchField(model)
                Row(Modifier.padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                    FilterChip(model.showIncompatible, { model.showIncompatible = !model.showIncompatible }, label = { Text("Include apps that won't run") },
                        leadingIcon = if (model.showIncompatible) {{ Icon(Icons.Filled.Check, null, Modifier.size(18.dp)) }} else null)
                }
                if (model.searching) LinearProgressIndicator(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp))
                if (model.message.isNotEmpty()) Text(model.message, Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                val rows = if (model.showIncompatible) model.results else model.results.filter { LegacyStore.incompatibility(it) == null }
                if (rows.isEmpty() && !model.searching) EmptyResults(model.results.isNotEmpty())
                else LazyColumn(Modifier.weight(1f).testTag("store_results"), contentPadding = PaddingValues(horizontal = 8.dp, vertical = 4.dp)) {
                    items(rows, key = { it.optInt("ipa_id") }) { app -> ResultRow(app, model) }
                }
            }
        }
    }
}

@Composable
private fun StoreTopBar(model: StoreModel, running: Boolean, onPickLocal: () -> Unit, onBack: () -> Unit) {
    var menu by remember { mutableStateOf(false) }
    Row(Modifier.fillMaxWidth().height(64.dp).padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = onBack, modifier = Modifier.testTag("store_close")) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
        Column(Modifier.weight(1f)) {
            Text("Legacy Store", style = MaterialTheme.typography.titleLarge)
            Text(model.target?.let { "${shortProductName(it.productType)} · iOS ${it.ios}" } ?: "No device selected",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Box {
            IconButton(onClick = { menu = true }, modifier = Modifier.testTag("store_menu")) { Icon(Icons.Filled.MoreVert, "More") }
            DropdownMenu(menu, { menu = false }) {
                DropdownMenuItem(text = { Text("Install an IPA file…") }, leadingIcon = { Icon(LtIcons.File, null) },
                    onClick = { menu = false; onPickLocal() }, enabled = running && !model.installing && model.servicesAvailable)
                DropdownMenuItem(text = { Text("Show last crash") }, leadingIcon = { Icon(LtIcons.Info, null) },
                    onClick = { menu = false; model.showLastCrash() }, enabled = running && model.servicesAvailable, modifier = Modifier.testTag("store_crash"))
            }
        }
    }
}

@Composable
private fun SearchField(model: StoreModel) {
    val keyboard = LocalSoftwareKeyboardController.current
    val search = { if (!model.searching && model.target != null) { keyboard?.hide(); model.search() } }
    TextField(model.query, { model.query = it }, Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp).testTag("store_query"),
        singleLine = true, shape = RoundedCornerShape(28.dp), placeholder = { Text("Search apps and games") },
        leadingIcon = { Icon(Icons.Filled.Search, null) },
        trailingIcon = {
            if (model.query.isNotEmpty()) IconButton(onClick = { model.query = "" }) { Icon(Icons.Filled.Close, "Clear") }
        },
        colors = TextFieldDefaults.colors(focusedIndicatorColor = Color.Transparent, unfocusedIndicatorColor = Color.Transparent, disabledIndicatorColor = Color.Transparent,
            focusedContainerColor = MaterialTheme.colorScheme.surfaceContainer, unfocusedContainerColor = MaterialTheme.colorScheme.surfaceContainer),
        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search), keyboardActions = KeyboardActions(onSearch = { search() }))
}

@Composable
private fun ColumnScope.EmptyResults(filtered: Boolean) {
    Column(Modifier.weight(1f).fillMaxWidth().padding(32.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
        Icon(LtIcons.Bag, null, Modifier.size(40.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(12.dp))
        Text(if (filtered) "Nothing here runs on this device" else "Find apps and games", style = MaterialTheme.typography.titleMedium)
        Text(if (filtered) "Include apps that won't run to see every result." else "Search the archive of classic iOS apps. Versions that run on this device are picked for you.",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center)
    }
}

@Composable
private fun ResultRow(app: JSONObject, model: StoreModel) {
    val problem = LegacyStore.incompatibility(app)
    val installedVersion = model.installed[app.optString("bundle_id")]
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).clickable { model.open(app) }.padding(horizontal = 8.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.alpha(if (problem == null) 1f else 0.5f)) { AppIcon(app.optString("icon_url").ifEmpty { null }, 52) }
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            Text(app.optString("name"), maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleSmall,
                color = if (problem == null) Color.Unspecified else MaterialTheme.colorScheme.onSurfaceVariant)
            Text(listOfNotNull(app.text("developer"), app.text("version"),
                app.optLong("size").takeIf { it > 0 }?.let(::byteSize)).joinToString(" · "),
                maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            when {
                problem != null -> Text(problem, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error, maxLines = 2)
                installedVersion != null -> Text("Installed $installedVersion", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
            }
        }
    }
}

@Composable
private fun ColumnScope.StoreDetails(model: StoreModel, app: JSONObject, running: Boolean) {
    val installedVersion = model.installed[app.optString("bundle_id")]
    Column(Modifier.weight(1f).padding(horizontal = 16.dp)) {
        Row(Modifier.padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            AppIcon(app.optString("icon_url").ifEmpty { null }, 72)
            Spacer(Modifier.width(16.dp))
            Column {
                Text(app.optString("name"), style = MaterialTheme.typography.titleLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text(app.text("developer") ?: "", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                installedVersion?.let { Text("Installed $it", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary) }
            }
        }
        Text("Versions that run on this device", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary,
            modifier = Modifier.padding(start = 4.dp, top = 12.dp, bottom = 8.dp))
        val rows = model.copies
        Column(Modifier.weight(1f, fill = false).fillMaxWidth().clip(RoundedCornerShape(20.dp)).background(MaterialTheme.colorScheme.surfaceContainer)) {
            if (rows == null) LinearProgressIndicator(Modifier.fillMaxWidth().padding(16.dp))
            else if (rows.isEmpty()) Text("None found.", Modifier.padding(16.dp), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            // The copy selected first (the search row's, or its stand-in) can sit far down the list: show it.
            val versionList = rememberLazyListState()
            LaunchedEffect(rows) { rows?.indexOfFirst { it.ipaId == model.selected }?.takeIf { it > 0 }?.let { versionList.scrollToItem(it) } }
            LazyColumn(Modifier.testTag("store_versions"), state = versionList, contentPadding = PaddingValues(vertical = 6.dp)) {
                items(rows ?: emptyList(), key = { it.ipaId }) { copy ->
                    val twin = (rows ?: emptyList()).count { it.version == copy.version } > 1
                    Row(Modifier.fillMaxWidth().clickable { model.choose(copy.ipaId) }.padding(horizontal = 8.dp, vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(model.selected == copy.ipaId, { model.choose(copy.ipaId) })
                        Column {
                            Text(copy.version + if (twin) " · copy ${copy.ipaId}" else "", style = MaterialTheme.typography.bodyLarge)
                            Text(listOfNotNull(copy.size?.let(::byteSize), copy.minimumOS?.let { "iOS $it or later" }).joinToString(" · "),
                                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
        }
        Spacer(Modifier.height(12.dp))
        val note = @Composable { text: String, error: Boolean ->
            Text(text, Modifier.padding(horizontal = 4.dp, vertical = 2.dp), style = MaterialTheme.typography.bodySmall,
                color = if (error) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
        }
        model.problem?.let { note(it, true) }
        val chosenVersion = rows?.firstOrNull { it.ipaId == model.selected }?.version
        if (installedVersion != null && chosenVersion != null) {
            val a = LegacyStore.version(chosenVersion.substringBefore(' ')); val b = LegacyStore.version(installedVersion)
            if (a != null && b != null && LegacyStore.newer(b, a)) note("Version $installedVersion is installed. An older version may not read its data.", false)
        }
        if (!running) note("Start the device to install.", false)
        else if (!model.servicesAvailable) note("App installation is not included in this build.", true)
        Spacer(Modifier.height(8.dp))
        Button(onClick = { model.candidate?.let { model.install(it) } }, Modifier.align(Alignment.CenterHorizontally).height(52.dp).testTag("store_install"),
            contentPadding = PaddingValues(horizontal = 40.dp), enabled = running && model.servicesAvailable && model.candidate != null && !model.installing) {
            Text(if (installedVersion != null) "Reinstall" else "Install", style = MaterialTheme.typography.titleMedium)
        }
        Spacer(Modifier.height(12.dp))
    }
}

@Composable
private fun StoreCard(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) =
    Column(modifier.fillMaxWidth().padding(vertical = 6.dp).clip(RoundedCornerShape(20.dp)).background(MaterialTheme.colorScheme.surfaceContainerHigh)
        .padding(horizontal = 16.dp, vertical = 12.dp), content = content)

@Composable
private fun InstallCard(job: InstallJob, model: StoreModel) {
    StoreCard(Modifier.testTag("store_job")) {
        Text(job.name, style = MaterialTheme.typography.titleSmall)
        Text(job.error ?: job.phase, style = MaterialTheme.typography.bodySmall,
            color = if (job.error != null) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
        if (job.error == null && !job.done) {
            if (job.progress != null) LinearProgressIndicator(progress = { job.progress.coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth().padding(top = 8.dp))
            else LinearProgressIndicator(Modifier.fillMaxWidth().padding(top = 8.dp))
        }
        if (job.cancellable || job.done || job.error != null) Row {
            Spacer(Modifier.weight(1f))
            if (job.cancellable) TextButton(onClick = { model.cancel() }) { Text("Cancel") }
            if (job.done || job.error != null) TextButton(onClick = { model.dismissJob() }) { Text("OK") }
        }
    }
}

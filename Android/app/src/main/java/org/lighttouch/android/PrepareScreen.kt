package org.lighttouch.android

import android.os.Bundle
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.testTagsAsResourceId
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

internal enum class PrepareStep { DEVICE, VERSION, FILES }

/** The prepare-from-IPSW flow's choices; the activity owns it so pickers, the download and the firmware worker can update it. */
internal class PrepareState {
    var catalog by mutableStateOf<List<CatalogFirmware>>(emptyList())
    /** Firmware ids the on-phone preparer implements (FirmwareNative.supports). */
    var supported by mutableStateOf<Set<String>>(emptySet())
    var step by mutableStateOf(PrepareStep.DEVICE)
    var productType by mutableStateOf("")
    var firmwareId by mutableStateOf("")
    /** A stock IPSW on this phone whose SHA-1 matched [ipswFor]'s catalog entry. */
    var ipswPath by mutableStateOf("")
    var ipswFor by mutableStateOf("")
    /** Copying and checking an IPSW the user chose; empty when idle. */
    var ipswChecking by mutableStateOf("")
    var ipswError by mutableStateOf("")
    var romPath by mutableStateOf("")
    var romLabel by mutableStateOf("")
    var romChecking by mutableStateOf(false)
    var romError by mutableStateOf("")
    var preparation by mutableStateOf<Bundle?>(null)
    var importError by mutableStateOf("")
    val firmware get() = catalog.firstOrNull { it.id == firmwareId }
    val ipswReady get() = ipswPath.isNotEmpty() && ipswFor == firmwareId
    val romReady get() = firmware?.let { !needsBootrom(it.board) } == true || romPath.isNotEmpty()
}

/** Order devices appear in: iPods, iPhones, then the iPad, each oldest first. */
private val deviceOrder = listOf("iPod1,1", "iPod2,1", "iPod3,1", "iPod4,1", "iPhone1,1", "iPhone1,2", "iPhone2,1", "iPhone3,1", "iPad1,1")

/** Version strings compared numerically: 3.1.10 after 3.1.3. */
private val versionOrder = Comparator<CatalogFirmware> { a, b ->
    val x = a.version.split('.').map { it.toIntOrNull() ?: 0 }; val y = b.version.split('.').map { it.toIntOrNull() ?: 0 }
    (0 until maxOf(x.size, y.size)).map { (x.getOrElse(it) { 0 }).compareTo(y.getOrElse(it) { 0 }) }.firstOrNull { it != 0 } ?: a.build.compareTo(b.build)
}

/** The preparer's stage names, as a person would say them. */
private fun stageText(stage: String) = when {
    stage == "verify IPSW" -> "Checking the IPSW"
    stage == "verify" -> "Checking the prepared files"
    stage == "decrypt rootfs" -> "Unpacking iOS"
    stage == "install baseline system edits" -> "Adding LightTouch's guest tools"
    stage == "filesystem" -> "Building the file system"
    stage == "write NAND" -> "Writing the device's storage"
    stage == "flush transaction" -> "Saving"
    stage == "promote transaction" -> "Finishing up"
    else -> stage.replaceFirstChar { it.uppercase() }.ifEmpty { "Starting" }
}

/**
 * Builds a device on this phone from a stock IPSW: pick the device, then the iOS version, then get
 * the IPSW (downloaded, or one already on the phone) and the boot file, and prepare it.
 */
@OptIn(androidx.compose.ui.ExperimentalComposeUiApi::class)
@Composable
internal fun PrepareScreen(state: PrepareState, busy: Boolean, preparing: Boolean, onDownload: () -> Unit, onChooseIpsw: () -> Unit,
                           onChooseRom: () -> Unit, onDownloadRom: () -> Unit, onPrepare: () -> Unit, onCancel: () -> Unit, onRetryImport: () -> Unit, onClose: () -> Unit) {
    val run = state.preparation?.getString("state")
    val showProgress = preparing || run in listOf("complete", "failed", "cancelled")
    val back = {
        when {
            preparing -> {}
            showProgress -> onClose()
            state.step == PrepareStep.FILES -> state.step = PrepareStep.VERSION
            state.step == PrepareStep.VERSION -> state.step = PrepareStep.DEVICE
            else -> onClose()
        }
    }
    BackHandler { back() }
    Surface(Modifier.fillMaxSize().semantics { testTagsAsResourceId = true }.testTag("prepare"), color = MaterialTheme.colorScheme.background) {
        // The setup wizard's backdrop, so moving between the two reads as one flow.
        Box(Modifier.fillMaxSize().background(Brush.verticalGradient(listOf(MaterialTheme.colorScheme.primary.copy(alpha = 0.10f), Color.Transparent))))
        Column(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing)) {
            Row(Modifier.fillMaxWidth().padding(4.dp), verticalAlignment = Alignment.CenterVertically) {
                if (!preparing) IconButton(onClick = back, modifier = Modifier.testTag("prepare_back")) {
                    if (showProgress || state.step == PrepareStep.DEVICE) Icon(Icons.Filled.Close, "Close") else Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back")
                } else Spacer(Modifier.size(48.dp))
            }
            Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 24.dp)) {
                when {
                    showProgress -> Progress(state, preparing, busy, onPrepare, onCancel, onRetryImport, onClose)
                    state.step == PrepareStep.DEVICE -> DeviceStep(state)
                    state.step == PrepareStep.VERSION -> VersionStep(state)
                    else -> FilesStep(state, busy, onDownload, onChooseIpsw, onChooseRom, onDownloadRom, onPrepare)
                }
                Spacer(Modifier.height(24.dp))
            }
        }
    }
}

@Composable
private fun Heading(title: String, detail: String) {
    Text(title, style = MaterialTheme.typography.headlineMedium)
    Spacer(Modifier.height(8.dp))
    Text(detail, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    Spacer(Modifier.height(20.dp))
}

@Composable
private fun SectionLabel(text: String) {
    Text(text, Modifier.padding(top = 16.dp, bottom = 6.dp), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

@Composable
private fun ChoiceRow(icon: ImageVector?, title: String, detail: String, enabled: Boolean, tag: String, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp).clip(RoundedCornerShape(16.dp)).background(MaterialTheme.colorScheme.surfaceContainer)
        .clickable(enabled = enabled, onClick = onClick).alpha(if (enabled) 1f else 0.45f).padding(16.dp).testTag(tag),
        verticalAlignment = Alignment.CenterVertically) {
        if (icon != null) { Icon(icon, null, Modifier.size(26.dp), tint = MaterialTheme.colorScheme.primary); Spacer(Modifier.width(16.dp)) }
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun DeviceStep(state: PrepareState) {
    Heading("Choose a device", "Pick the device to emulate. You'll choose its iOS version next.")
    val devices = state.catalog.groupBy { it.productType }.entries.sortedBy { deviceOrder.indexOf(it.key).let { i -> if (i < 0) 99 else i } }
    val (ready, later) = devices.partition { (_, firmwares) -> firmwares.any { it.id in state.supported } }
    for ((product, firmwares) in ready) {
        val versions = firmwares.filter { it.id in state.supported }.sortedWith(versionOrder).joinToString(", ") { "iOS ${it.label}" }
        ChoiceRow(if (product.startsWith("iPad")) LtIcons.Tablet else LtIcons.Phone, shortProductName(product), versions, true, "prepare_device_$product") {
            state.productType = product
            if (state.firmware?.productType != product) state.firmwareId = ""
            state.step = PrepareStep.VERSION
        }
    }
    if (later.isNotEmpty()) {
        SectionLabel("Not supported yet")
        for ((product, firmwares) in later) {
            val sorted = firmwares.filter { it.prerelease.isEmpty() }.sortedWith(versionOrder)
            val range = if (sorted.isEmpty()) "" else "iOS ${sorted.first().version} to ${sorted.last().version}"
            ChoiceRow(if (product.startsWith("iPad")) LtIcons.Tablet else LtIcons.Phone, shortProductName(product), range, false, "prepare_device_$product") {}
        }
    }
    if (state.catalog.isEmpty()) Text("Loading the firmware list…", style = MaterialTheme.typography.bodyMedium)
}

@Composable
private fun VersionStep(state: PrepareState) {
    val name = shortProductName(state.productType)
    Heading("Choose iOS for $name", "LightTouch prepares the versions below on this phone.")
    val firmwares = state.catalog.filter { it.productType == state.productType }.sortedWith(versionOrder.reversed())
    val (ready, later) = firmwares.partition { it.id in state.supported }
    for (firmware in ready) ChoiceRow(null, "iOS ${firmware.label}", released(firmware), true, "prepare_version_${firmware.id}") {
        state.firmwareId = firmware.id; state.step = PrepareStep.FILES
    }
    val stable = later.filter { it.prerelease.isEmpty() }
    if (stable.isNotEmpty()) {
        SectionLabel("Not supported yet")
        for (firmware in stable) ChoiceRow(null, "iOS ${firmware.label}", released(firmware), false, "prepare_version_${firmware.id}") {}
    }
}

private fun released(firmware: CatalogFirmware): String {
    val date = firmware.released.split('-').takeIf { it.size >= 2 }?.let { (year, month) ->
        val months = listOf("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
        months.getOrNull((month.toIntOrNull() ?: 0) - 1)?.let { "$it $year" }
    }
    return listOfNotNull(date?.let { "Released $it" }, "build ${firmware.build}").joinToString(" · ")
}

@Composable
private fun ColumnScope.FilesStep(state: PrepareState, busy: Boolean, onDownload: () -> Unit, onChooseIpsw: () -> Unit, onChooseRom: () -> Unit,
                                  onDownloadRom: () -> Unit, onPrepare: () -> Unit) {
    val firmware = state.firmware ?: return
    val name = shortProductName(firmware.productType)
    Heading("$name, iOS ${firmware.label}", "LightTouch needs Apple's stock IPSW for this version" +
        (if (needsBootrom(firmware.board)) ", and the device's boot file." else "."))
    // IPSW
    Card(icon = LtIcons.Layers, title = "iOS firmware (IPSW)", done = state.ipswReady,
        detail = if (state.ipswReady) "${java.io.File(state.ipswPath).name} · verified" else "${firmware.fileName} · ${firmware.bytes / 1_000_000} MB") {
        val downloading = IpswDownload.running && IpswDownload.firmwareId == firmware.id
        when {
            state.ipswReady -> TextButton(onClick = onChooseIpsw, enabled = !busy) { Text("Use a different file") }
            downloading -> {
                Text(IpswDownload.status, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.height(8.dp))
                if (IpswDownload.progress >= 0f) LinearProgressIndicator(progress = { IpswDownload.progress }, Modifier.fillMaxWidth())
                else LinearProgressIndicator(Modifier.fillMaxWidth())
                TextButton(onClick = { IpswDownload.cancel() }, modifier = Modifier.testTag("prepare_cancel_download")) { Text("Cancel download") }
            }
            state.ipswChecking.isNotEmpty() -> {
                Text(state.ipswChecking, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.height(8.dp)); LinearProgressIndicator(Modifier.fillMaxWidth())
            }
            else -> {
                val error = state.ipswError.ifEmpty { IpswDownload.status.takeIf { IpswDownload.firmwareId == firmware.id } ?: "" }
                if (error.isNotEmpty()) Text(error, Modifier.padding(bottom = 8.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = onDownload, enabled = !busy && !IpswDownload.running, modifier = Modifier.testTag("prepare_download")) {
                        Icon(LtIcons.Download, null, Modifier.size(18.dp)); Spacer(Modifier.width(8.dp)); Text("Download")
                    }
                    OutlinedButton(onClick = onChooseIpsw, enabled = !busy, modifier = Modifier.testTag("prepare_choose_ipsw")) { Text("Choose a file") }
                }
                Text("Downloads come from Apple's servers when ipsw.me lists them, otherwise from the Internet Archive.",
                    Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
    // Boot file
    if (needsBootrom(firmware.board)) Card(icon = LtIcons.Chip, title = "Boot file", done = state.romReady,
        detail = if (state.romReady) state.romLabel else "The $name's boot ROM (64 KB). It is part of the chip, not the IPSW.") {
        when {
            state.romReady -> TextButton(onClick = onChooseRom, enabled = !busy) { Text("Use a different file") }
            state.romChecking -> LinearProgressIndicator(Modifier.fillMaxWidth())
            else -> {
                if (state.romError.isNotEmpty()) Text(state.romError, Modifier.padding(bottom = 8.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = onDownloadRom, enabled = !busy, modifier = Modifier.testTag("prepare_download_rom")) {
                        Icon(LtIcons.Download, null, Modifier.size(18.dp)); Spacer(Modifier.width(8.dp)); Text("Download")
                    }
                    OutlinedButton(onClick = onChooseRom, enabled = !busy, modifier = Modifier.testTag("prepare_choose_rom")) { Text("Choose a file") }
                }
                Text("Downloads come from securerom.fun's archive of boot ROM dumps, or its GitHub copy.", Modifier.padding(top = 8.dp),
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
    Spacer(Modifier.height(12.dp))
    Text("Preparing runs on this phone, takes about a minute and needs about 1.5 GB free. LightTouch's guest tools are added for you.",
        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    Spacer(Modifier.height(20.dp))
    Button(onClick = onPrepare, enabled = !busy && state.ipswReady && state.romReady && state.ipswChecking.isEmpty(),
        modifier = Modifier.align(Alignment.CenterHorizontally).height(52.dp).testTag("start_preparation"), contentPadding = PaddingValues(horizontal = 32.dp)) {
        Text("Prepare device", style = MaterialTheme.typography.titleMedium)
    }
}

@Composable
private fun Card(icon: ImageVector, title: String, done: Boolean, detail: String, content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp).clip(RoundedCornerShape(16.dp)).background(MaterialTheme.colorScheme.surfaceContainer).padding(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(icon, null, Modifier.size(24.dp), tint = if (done) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.width(16.dp))
            Column(Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.titleSmall)
                Text(detail, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (done) Icon(Icons.Filled.CheckCircle, "Ready", tint = MaterialTheme.colorScheme.primary)
        }
        Spacer(Modifier.height(8.dp))
        content()
    }
}

@Composable
private fun Progress(state: PrepareState, preparing: Boolean, busy: Boolean, onPrepare: () -> Unit, onCancel: () -> Unit, onRetryImport: () -> Unit, onClose: () -> Unit) {
    val run = state.preparation
    val phase = run?.getString("state") ?: "starting"
    val firmware = state.firmware
    val title = firmware?.let { "${shortProductName(it.productType)}, iOS ${it.label}" } ?: "Your device"
    Column(Modifier.fillMaxWidth().padding(top = 48.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        when {
            preparing -> {
                Text("Preparing $title", style = MaterialTheme.typography.titleLarge, textAlign = TextAlign.Center)
                Spacer(Modifier.height(8.dp))
                val stage = run?.getString("stage") ?: ""
                Text(if (phase == "cancelling") "Cancelling…" else stageText(stage), Modifier.testTag("preparation_status"),
                    style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.height(24.dp))
                val done = run?.getLong("done") ?: 0L; val total = run?.getLong("total") ?: 0L
                // Checking the IPSW reports bytes read without a total; the catalog knows the size.
                val whole = if (total > 0) total else if (stage == "verify IPSW") firmware?.bytes ?: 0L else 0L
                if (whole > 0 && done > 0) LinearProgressIndicator(progress = { (done.toFloat() / whole).coerceIn(0f, 1f) }, Modifier.fillMaxWidth())
                else LinearProgressIndicator(Modifier.fillMaxWidth())
                Spacer(Modifier.height(12.dp))
                Text("This takes about a minute. You can leave LightTouch; preparation continues in the background.",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center)
                Spacer(Modifier.height(24.dp))
                OutlinedButton(onClick = onCancel, enabled = phase != "cancelling", modifier = Modifier.testTag("cancel_preparation")) { Text("Cancel") }
            }
            phase == "complete" && state.importError.isNotEmpty() -> {
                Text("$title is prepared", style = MaterialTheme.typography.titleLarge, textAlign = TextAlign.Center)
                Spacer(Modifier.height(8.dp))
                Text("It could not be added as a device: ${state.importError}", style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.error, textAlign = TextAlign.Center)
                Spacer(Modifier.height(24.dp))
                Button(onClick = onRetryImport, enabled = !busy) { Text("Try adding it again") }
            }
            phase == "complete" -> {
                CircularProgressIndicator(Modifier.size(40.dp))
                Spacer(Modifier.height(20.dp))
                Text("Adding $title", style = MaterialTheme.typography.titleLarge, textAlign = TextAlign.Center)
            }
            else -> {
                Text(if (phase == "cancelled") "Preparation cancelled" else "Preparation failed", style = MaterialTheme.typography.titleLarge)
                Spacer(Modifier.height(8.dp))
                run?.getString("error")?.takeIf { it.isNotEmpty() && phase != "cancelled" }?.let {
                    Text(it, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.error, textAlign = TextAlign.Center)
                }
                Spacer(Modifier.height(24.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = { state.preparation = null }) { Text("Back") }
                    Button(onClick = onPrepare, enabled = !busy && state.ipswReady && state.romReady) { Text("Try again") }
                }
            }
        }
    }
}

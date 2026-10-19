package org.lighttouch.android

import android.content.Intent
import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedContent
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.testTagsAsResourceId
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

internal enum class SetupStep { WELCOME, FILES, CREATING, DONE }
internal data class PickedFile(val uri: Uri, val name: String, val folder: Boolean = false)

/** Wizard state; the activity owns it so pickers and the import worker can update it. */
internal class SetupState {
    var step by mutableStateOf(SetupStep.WELCOME)
    var system by mutableStateOf<PickedFile?>(null)
    var tools by mutableStateOf<PickedFile?>(null)
    var rom by mutableStateOf<PickedFile?>(null)
    var name by mutableStateOf("")
    var phase by mutableStateOf("")
    var error by mutableStateOf("")
    var created by mutableStateOf<String?>(null)
    var scanned by mutableStateOf<String?>(null)
    fun reset(firstRun: Boolean) {
        step = if (firstRun) SetupStep.WELCOME else SetupStep.FILES
        system = null; tools = null; rom = null; name = ""; phase = ""; error = ""; created = null; scanned = null
    }
}

/**
 * First launch, and Add device later: welcome, choose the iOS system, guest tools and boot file
 * (or one folder holding them), then create the device and offer to start it.
 */
@OptIn(androidx.compose.ui.ExperimentalComposeUiApi::class)
@Composable
internal fun SetupWizard(state: SetupState, canCancel: Boolean, onPickFolder: () -> Unit, onPickSystem: () -> Unit,
                         onPickTools: () -> Unit, onPickRom: () -> Unit, onCreate: () -> Unit, onPrepare: () -> Unit,
                         onFinish: (start: Boolean) -> Unit, onCancel: () -> Unit) {
    BackHandler(enabled = state.step != SetupStep.CREATING) {
        when {
            state.step == SetupStep.FILES && !canCancel && state.system == null -> state.step = SetupStep.WELCOME
            canCancel && state.step != SetupStep.DONE -> onCancel()
            state.step == SetupStep.DONE -> onFinish(false)
        }
    }
    Surface(Modifier.fillMaxSize().semantics { testTagsAsResourceId = true }.testTag("setup"), color = MaterialTheme.colorScheme.background) {
        Box(Modifier.fillMaxSize().background(Brush.verticalGradient(listOf(MaterialTheme.colorScheme.primary.copy(alpha = 0.10f), Color.Transparent))))
        Column(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing)) {
            if (canCancel && state.step != SetupStep.CREATING) Row(Modifier.fillMaxWidth().padding(4.dp)) {
                IconButton(onClick = { if (state.step == SetupStep.DONE) onFinish(false) else onCancel() }) { Icon(Icons.Filled.Close, "Close") }
            }
            AnimatedContent(state.step, Modifier.weight(1f), label = "setup-step") { step ->
                when (step) {
                    SetupStep.WELCOME -> Welcome { state.step = SetupStep.FILES }
                    SetupStep.FILES -> Files(state, onPickFolder, onPickSystem, onPickTools, onPickRom, onCreate, onPrepare)
                    SetupStep.CREATING -> Creating(state.phase)
                    SetupStep.DONE -> Done(state.created ?: "Your device", onFinish)
                }
            }
        }
    }
}

@Composable
private fun Welcome(onNext: () -> Unit) {
    Column(Modifier.fillMaxSize().padding(horizontal = 28.dp), verticalArrangement = Arrangement.Center) {
        Box(Modifier.size(72.dp).background(MaterialTheme.colorScheme.primaryContainer, RoundedCornerShape(20.dp)), contentAlignment = Alignment.Center) {
            Icon(LtIcons.Phone, null, Modifier.size(38.dp), tint = MaterialTheme.colorScheme.onPrimaryContainer)
        }
        Spacer(Modifier.height(28.dp))
        Text("LightTouch", style = MaterialTheme.typography.headlineLarge)
        Spacer(Modifier.height(8.dp))
        Text("Classic iPod touch and iPhone software, running on your phone.", style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(36.dp))
        Text("You'll need three files from LightTouch's setup guide:", style = MaterialTheme.typography.bodyMedium)
        Spacer(Modifier.height(16.dp))
        Need(LtIcons.Layers, "iOS system", ".itbase, the prepared device image")
        Need(LtIcons.Puzzle, "Guest tools", ".itpack, which lets LightTouch install apps")
        Need(LtIcons.Chip, "Boot file", "bootrom.bin, the device's boot ROM")
        Spacer(Modifier.height(40.dp))
        Button(onClick = onNext, modifier = Modifier.align(Alignment.CenterHorizontally).height(52.dp).testTag("setup_start"), contentPadding = PaddingValues(horizontal = 32.dp)) {
            Text("Get started", style = MaterialTheme.typography.titleMedium)
        }
        GuideLink(Modifier.align(Alignment.CenterHorizontally).padding(top = 8.dp))
    }
}

@Composable
private fun Need(icon: ImageVector, title: String, detail: String) {
    Row(Modifier.padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Icon(icon, null, Modifier.size(22.dp), tint = MaterialTheme.colorScheme.primary)
        Spacer(Modifier.width(14.dp))
        Column {
            Text(title, style = MaterialTheme.typography.titleSmall)
            Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun GuideLink(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    TextButton(onClick = { context.startActivity(Intent(Intent.ACTION_VIEW,
        Uri.parse("https://github.com/blake-lucas/LightTouch/blob/main/docs/ASSETS.md"))) }, modifier = modifier) {
        Text("Where do I get these?")
    }
}

@Composable
private fun Files(state: SetupState, onPickFolder: () -> Unit, onPickSystem: () -> Unit, onPickTools: () -> Unit,
                  onPickRom: () -> Unit, onCreate: () -> Unit, onPrepare: () -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 24.dp)) {
        Spacer(Modifier.height(24.dp))
        Text("Add a device", style = MaterialTheme.typography.headlineMedium)
        Spacer(Modifier.height(8.dp))
        Text("Pick the folder you copied the files into, or choose each one.", style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(24.dp))
        Surface(onClick = onPickFolder, shape = RoundedCornerShape(20.dp), color = MaterialTheme.colorScheme.primaryContainer,
            modifier = Modifier.fillMaxWidth().testTag("setup_folder")) {
            Row(Modifier.padding(20.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(LtIcons.Folder, null, Modifier.size(28.dp), tint = MaterialTheme.colorScheme.onPrimaryContainer)
                Spacer(Modifier.width(16.dp))
                Column(Modifier.weight(1f)) {
                    Text("Choose a folder", style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.onPrimaryContainer)
                    Text(state.scanned ?: "LightTouch finds the three files in it", style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onPrimaryContainer.copy(alpha = 0.8f))
                }
            }
        }
        Spacer(Modifier.height(20.dp))
        Text("Or choose each file", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(8.dp))
        FileRow(LtIcons.Layers, "iOS system", "Required · .itbase", state.system, onPickSystem, "setup_system")
        FileRow(LtIcons.Puzzle, "Guest tools", "Recommended · .itpack", state.tools, onPickTools, "setup_tools")
        FileRow(LtIcons.Chip, "Boot file", "Required for iPod touch 2G · bootrom.bin", state.rom, onPickRom, "setup_rom")
        if (state.system != null && state.rom == null) Text("Without the boot file the iPod touch 2G will not start; you can still add it now.",
            style = MaterialTheme.typography.bodySmall, color = LtColors.Warning, modifier = Modifier.padding(top = 4.dp))
        Spacer(Modifier.height(16.dp))
        OutlinedTextField(state.name, { if (it.length <= 60) state.name = it }, Modifier.fillMaxWidth().testTag("setup_name"), singleLine = true,
            label = { Text("Name (optional)") }, placeholder = { Text("Named after the model and iOS version") })
        if (state.error.isNotEmpty()) Text(state.error, Modifier.padding(top = 12.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
        Spacer(Modifier.height(20.dp))
        Button(onClick = onCreate, enabled = state.system != null, modifier = Modifier.align(Alignment.CenterHorizontally).height(52.dp).testTag("setup_create"),
            contentPadding = PaddingValues(horizontal = 32.dp)) { Text("Create device", style = MaterialTheme.typography.titleMedium) }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            GuideLink()
            TextButton(onClick = onPrepare) { Text("Prepare from an IPSW") }
        }
        Spacer(Modifier.height(16.dp))
    }
}

@Composable
private fun FileRow(icon: ImageVector, title: String, hint: String, picked: PickedFile?, onPick: () -> Unit, tag: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp).clip(RoundedCornerShape(16.dp))
        .background(MaterialTheme.colorScheme.surfaceContainer).clickable(onClick = onPick).padding(16.dp).testTag(tag),
        verticalAlignment = Alignment.CenterVertically) {
        Icon(icon, null, Modifier.size(24.dp), tint = if (picked != null) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.width(16.dp))
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleSmall)
            Text(picked?.name ?: hint, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis,
                color = if (picked != null) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (picked != null) Icon(Icons.Filled.CheckCircle, "Chosen", tint = MaterialTheme.colorScheme.primary)
        else Box(Modifier.size(22.dp).border(1.5.dp, MaterialTheme.colorScheme.outline, CircleShape))
    }
}

@Composable
private fun Creating(phase: String) {
    Column(Modifier.fillMaxSize().padding(32.dp), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
        CircularProgressIndicator(Modifier.size(48.dp), strokeWidth = 4.dp)
        Spacer(Modifier.height(28.dp))
        Text("Creating your device", style = MaterialTheme.typography.titleLarge)
        Spacer(Modifier.height(8.dp))
        Text(phase.ifEmpty { "Copying files…" }, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center)
        Spacer(Modifier.height(8.dp))
        Text("This takes a minute or two. Keep LightTouch open.", style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center)
    }
}

@Composable
private fun Done(name: String, onFinish: (Boolean) -> Unit) {
    Column(Modifier.fillMaxSize().padding(horizontal = 28.dp), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
        Icon(Icons.Filled.CheckCircle, null, Modifier.size(72.dp), tint = MaterialTheme.colorScheme.primary)
        Spacer(Modifier.height(24.dp))
        Text("$name is ready", style = MaterialTheme.typography.titleLarge, textAlign = TextAlign.Center)
        Spacer(Modifier.height(8.dp))
        Text("The first start takes longer while iOS sets itself up.", style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center)
        Spacer(Modifier.height(36.dp))
        Button(onClick = { onFinish(true) }, modifier = Modifier.height(52.dp).testTag("setup_launch"), contentPadding = PaddingValues(start = 24.dp, end = 32.dp)) {
            Icon(LtIcons.Play, null, Modifier.size(20.dp)); Spacer(Modifier.width(10.dp)); Text("Start it now", style = MaterialTheme.typography.titleMedium)
        }
        TextButton(onClick = { onFinish(false) }, modifier = Modifier.padding(top = 8.dp)) { Text("Not now") }
    }
}

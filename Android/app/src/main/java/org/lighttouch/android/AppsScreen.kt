package org.lighttouch.android

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.testTagsAsResourceId
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

/**
 * The apps on the selected device, opened from Settings: what the guest's installer lists, what each app says
 * about itself, and removing one. The list is the Store's ([StoreModel.installedList]), read from the running
 * guest over the USB bridge; removal is the guest's own uninstall, so it works whether or not the Store knows the app.
 */
@OptIn(androidx.compose.ui.ExperimentalComposeUiApi::class)
@Composable
internal fun AppsScreen(model: StoreModel, running: Boolean, onBack: () -> Unit) {
    val installed = model.installedList
    var viewing by remember { mutableStateOf<InstalledApp?>(null) }
    var confirming by remember { mutableStateOf<InstalledApp?>(null) }
    val removing = model.removing
    // Asked again whenever this page opens on a running guest; a recent list stands otherwise.
    LaunchedEffect(running) { if (running) installed.refresh() }
    // The device that answered a removal is gone from the list; a page about it has nothing left to show.
    LaunchedEffect(installed.list) { viewing?.let { app -> if (installed.list?.none { it.bundleId == app.bundleId } == true) viewing = null } }
    BackHandler(enabled = viewing == null && confirming == null, onBack = onBack)

    confirming?.let { app ->
        AlertDialog(onDismissRequest = { confirming = null },
            icon = { Icon(Icons.Filled.Delete, null) },
            title = { Text("Remove ${app.title}?", style = MaterialTheme.typography.titleMedium) },
            text = { Text("The app and everything it saved are deleted from the device. An app you got from the Legacy Store can be installed again from there.") },
            confirmButton = { TextButton(onClick = { confirming = null; model.remove(app) }, modifier = Modifier.testTag("apps_confirm_remove")) { Text("Remove") } },
            dismissButton = { TextButton(onClick = { confirming = null }) { Text("Cancel") } })
    }
    viewing?.let { app -> AppDetails(app, onClose = { viewing = null }, onRemove = { viewing = null; confirming = app }) }

    Surface(Modifier.fillMaxSize().testTag("apps").semantics { testTagsAsResourceId = true }, color = MaterialTheme.colorScheme.background) {
        Column(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing)) {
            Row(Modifier.fillMaxWidth().height(64.dp).padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onBack, modifier = Modifier.testTag("apps_back")) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
                Column(Modifier.weight(1f)) {
                    Text("Apps", style = MaterialTheme.typography.titleLarge)
                    Text(installed.list?.let { "${it.size} installed" } ?: "On the device", style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                if (running) IconButton(onClick = { installed.refresh(force = true) }, enabled = !installed.loading,
                    modifier = Modifier.testTag("apps_refresh")) { Icon(LtIcons.History, "Read the list again") }
            }
            model.removeError?.let { message ->
                Row(Modifier.padding(horizontal = 16.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(message, Modifier.weight(1f), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                    TextButton(onClick = { model.removeError = null }) { Text("Dismiss") }
                }
            }
            val list = installed.list
            when {
                !running -> AppsMessage("Start the device to see its apps", "The apps on a device are read from it while it runs.")
                !model.servicesAvailable -> AppsMessage("Not available in this build", "Reading a device's apps is not included in this build.")
                installed.error != null && list == null -> AppsMessage("Couldn't read this device's apps", installed.error ?: "", retry = { installed.refresh(true) })
                list == null -> LinearProgressIndicator(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp))
                list.isEmpty() -> AppsMessage("No apps added yet", "Apps you install from the Legacy Store or from an IPA file show up here.")
                else -> LazyColumn(Modifier.weight(1f).testTag("apps_list"), contentPadding = PaddingValues(horizontal = 8.dp, vertical = 4.dp)) {
                    items(list, key = { it.bundleId }) { app ->
                        AppRow(app, busy = removing == app.bundleId, onView = { viewing = app }, onRemove = { confirming = app })
                    }
                }
            }
        }
    }
}

@Composable
private fun AppRow(app: InstalledApp, busy: Boolean, onView: () -> Unit, onRemove: () -> Unit) {
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).clickable(onClick = onView)
        .padding(start = 8.dp, end = 4.dp, top = 8.dp, bottom = 8.dp).testTag("apps_row"), verticalAlignment = Alignment.CenterVertically) {
        AppIcon(null, 52, app.title)
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            Text(app.title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleSmall)
            Text(app.version?.let { "Version $it" } ?: app.bundleId, maxLines = 1, overflow = TextOverflow.Ellipsis,
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (busy) {
            CircularProgressIndicator(Modifier.size(24.dp).testTag("apps_removing"), strokeWidth = 2.dp)
            Spacer(Modifier.width(16.dp))
        } else {
            IconButton(onClick = onRemove, modifier = Modifier.testTag("apps_remove")) {
                Icon(Icons.Filled.Delete, "Remove ${app.title}", tint = MaterialTheme.colorScheme.error)
            }
        }
    }
}

/** One app, as the device describes it, with the way to remove it. */
@Composable
private fun AppDetails(app: InstalledApp, onClose: () -> Unit, onRemove: () -> Unit) {
    AlertDialog(onDismissRequest = onClose,
        title = { Text(app.title, style = MaterialTheme.typography.titleMedium, maxLines = 2, overflow = TextOverflow.Ellipsis) },
        text = {
            Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState()).testTag("apps_details")) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    AppIcon(null, 64, app.title)
                    Spacer(Modifier.width(16.dp))
                    Column {
                        Text(app.version?.let { "Version $it" } ?: "Version unknown", style = MaterialTheme.typography.bodyMedium)
                        if (app.shortVersion != null && app.build != null && app.build != app.shortVersion)
                            Text("Build ${app.build}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                Spacer(Modifier.height(16.dp))
                Text("Identifier", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(app.bundleId, style = MaterialTheme.typography.bodyMedium)
                Spacer(Modifier.height(16.dp))
                Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(MaterialTheme.colorScheme.surfaceContainer).padding(12.dp)) {
                    Text("Removing an app deletes it and its saved data from the device.", style = MaterialTheme.typography.bodySmall)
                }
            }
        },
        confirmButton = { TextButton(onClick = onRemove) { Icon(Icons.Filled.Delete, null, Modifier.size(18.dp)); Spacer(Modifier.width(6.dp)); Text("Remove") } },
        dismissButton = { TextButton(onClick = onClose) { Text("Close") } })
}

@Composable
private fun ColumnScope.AppsMessage(title: String, body: String, retry: (() -> Unit)? = null) {
    Column(Modifier.weight(1f).fillMaxWidth().padding(32.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
        Icon(LtIcons.Bag, null, Modifier.size(40.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(12.dp))
        Text(title, style = MaterialTheme.typography.titleMedium, textAlign = TextAlign.Center)
        Text(body, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center)
        retry?.let { TextButton(onClick = it) { Text("Try again") } }
    }
}

package org.lighttouch.android

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.provider.MediaStore
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** What an instrumented engine (a CI PGO pass, LT_PGO_FILE) recorded in this install, newest first. */
internal fun pgoProfiles(filesDir: File): List<File> =
    filesDir.listFiles { f -> f.isFile && f.name.startsWith("pgo") && f.name.endsWith(".profraw") }
        ?.sortedByDescending { it.lastModified() } ?: emptyList()

/** Copy one of them into Downloads/LightTouch, where it can be shared off the phone; returns its content URI. */
internal fun writePgoProfile(context: Context, profile: File): Uri {
    val stamp = SimpleDateFormat("yyyyMMdd-HHmmss", Locale.ROOT).format(Date())
    val values = ContentValues().apply {
        put(MediaStore.Downloads.DISPLAY_NAME, "lighttouch-pgo-$stamp.profraw")
        put(MediaStore.Downloads.MIME_TYPE, "application/octet-stream")
        put(MediaStore.Downloads.RELATIVE_PATH, "Download/LightTouch")
        put(MediaStore.Downloads.IS_PENDING, 1)
    }
    val resolver = context.contentResolver
    val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values) ?: error("Cannot create the profile in Downloads")
    try {
        resolver.openOutputStream(uri)?.use { out -> profile.inputStream().use { it.copyTo(out) } } ?: error("Cannot write the profile")
        values.clear(); values.put(MediaStore.Downloads.IS_PENDING, 0)
        resolver.update(uri, values, null, null)
    } catch (e: Exception) { resolver.delete(uri, null, null); throw e }
    return uri
}

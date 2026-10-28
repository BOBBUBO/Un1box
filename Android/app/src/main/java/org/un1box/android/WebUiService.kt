package org.un1box.android

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import java.net.NetworkInterface

/**
 * The Web UI's own preferences: whether it is enabled, the port it serves on, and the token every
 * request must carry. The token is generated once, on the first request for it, and shown in the
 * app's Settings screen as part of the full address to open — the server never accepts a request
 * without it, so another device on the network cannot reach the Web UI by port scanning it.
 */
internal object WebUiPrefs {
    const val DEFAULT_PORT = 8160

    private fun prefs(context: Context) = context.getSharedPreferences("webui", Context.MODE_PRIVATE)

    fun enabled(context: Context) = prefs(context).getBoolean("enabled", false)
    fun setEnabled(context: Context, on: Boolean) = prefs(context).edit().putBoolean("enabled", on).apply()

    fun port(context: Context) = prefs(context).getInt("port", DEFAULT_PORT)
    fun setPort(context: Context, port: Int) = prefs(context).edit().putInt("port", port).apply()

    fun token(context: Context): String = prefs(context).getString("token", null) ?: resetToken(context)

    fun resetToken(context: Context): String {
        val values = ByteArray(16)
        java.security.SecureRandom().nextBytes(values)
        val token = values.joinToString("") { "%02x".format(it) }
        prefs(context).edit().putString("token", token).apply()
        return token
    }
}

/** What the app's Settings screen shows: whether the server is up, and where it is listening. */
internal object WebUiState {
    @Volatile var running = false
        private set
    @Volatile var port = 0
        private set
    private val listeners = java.util.concurrent.CopyOnWriteArraySet<() -> Unit>()

    /** Registers [listener]; returns the function that unregisters it. */
    fun listen(listener: () -> Unit): () -> Unit {
        listeners.add(listener)
        return { listeners.remove(listener) }
    }

    internal fun publish(up: Boolean, port: Int) {
        running = up
        WebUiState.port = if (up) port else 0
        for (listener in listeners) runCatching(listener)
    }
}

/** The private addresses this phone can be reached on, for the Settings screen and the notification. */
internal fun webUiAddresses(context: Context, port: Int): List<String> {
    val token = WebUiPrefs.token(context)
    val addresses = mutableListOf<String>()
    try {
        for (interfaces in NetworkInterface.getNetworkInterfaces()) {
            for (address in interfaces.inetAddresses) {
                val text = address.hostAddress ?: continue
                if (!address.isLoopbackAddress && ':' !in text) addresses += text
            }
        }
    } catch (_: Exception) { }
    return addresses.sorted().map { "http://$it:$port/?token=$token" }
}

/**
 * The service that keeps the Web UI's server alive while it is on: a foreground service, like the
 * engine and preparation workers, so Android neither kills it mid-session nor throttles it in the
 * background. It serves every request itself; the engine and firmware workers stay where they are
 * (their own processes), reached through the same binder interfaces the activity uses, so the Web
 * UI drives exactly the same code the app does.
 */
class WebUiService : Service() {
    private var backend: WebUi? = null
    private var server: WebUiServer? = null

    override fun onBind(intent: Intent): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            WebUiPrefs.setEnabled(this, false)
            stopSelf()
            return START_NOT_STICKY
        }
        // A start means enabled: setEnabled(true) ran before startForegroundService.
        WebUiPrefs.setEnabled(this, true)
        startServer()
        foreground()
        return START_NOT_STICKY
    }

    private fun startServer() {
        val port = WebUiPrefs.port(this)
        val app = backend ?: WebUi(this).also { backend = it }
        val existing = server
        if (existing != null && existing.running && WebUiState.port == port) return
        existing?.stop()
        val next = WebUiServer(port, WebUiPrefs.token(this), app::route)
        next.start()
        server = next
        app.start()
        WebUiState.publish(true, port)
    }

    override fun onDestroy() {
        server?.stop()
        server = null
        backend?.close()
        backend = null
        WebUiState.publish(false, 0)
        super.onDestroy()
    }

    /** Ongoing, because the Web UI is a service the user turned on, not a result to clear away. */
    private fun foreground() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, "Web UI", NotificationManager.IMPORTANCE_LOW))
        val port = WebUiPrefs.port(this)
        val address = webUiAddresses(this, port).firstOrNull() ?: "http://<this phone>:$port"
        val open = PendingIntent.getActivity(this, 1, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val stop = PendingIntent.getService(this, 2, Intent(this, WebUiService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        startForeground(NOTIFICATION, Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_menu_view)
            .setContentTitle("Un1box Web UI")
            .setContentText(address)
            .setVisibility(Notification.VISIBILITY_SECRET)
            .setContentIntent(open).setOngoing(true)
            .addAction(Notification.Action.Builder(null, "Stop", stop).build())
            .build())
    }

    companion object {
        private const val CHANNEL = "webui"
        private const val NOTIFICATION = 3
        private const val ACTION_STOP = "org.un1box.android.STOP"

        /** The Settings switch: starts or stops the server, and is remembered across launches. */
        fun setEnabled(context: Context, on: Boolean) {
            WebUiPrefs.setEnabled(context, on)
            if (on) context.startForegroundService(Intent(context, WebUiService::class.java))
            else context.startService(Intent(context, WebUiService::class.java).setAction(ACTION_STOP))
        }
    }
}

/** A Bundle as JSON, the shape the worker's receipts and observations already use. */
internal fun bundleToJson(bundle: Bundle): org.json.JSONObject = org.json.JSONObject().apply {
    for (key in bundle.keySet()) when (val value = bundle.get(key)) {
        null -> put(key, org.json.JSONObject.NULL)
        is Bundle -> put(key, bundleToJson(value))
        is Boolean, is Int, is Long, is Double -> put(key, value)
        is Float -> put(key, value.toDouble())
        is String -> put(key, value)
        is IntArray -> put(key, org.json.JSONArray(value.toList()))
        is LongArray -> put(key, org.json.JSONArray(value.toList()))
        is Array<*> -> put(key, org.json.JSONArray(value.map { it.toString() }))
        else -> put(key, value.toString())
    }
}

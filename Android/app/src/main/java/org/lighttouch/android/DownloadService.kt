package org.lighttouch.android

import android.app.*
import android.content.Context
import android.content.Intent
import android.os.IBinder
import android.os.PowerManager

/**
 * Keeps the app's own process running while it downloads or copies an IPSW. Without a foreground service
 * Android freezes a backgrounded app within seconds, and the transfer fails. Held for each transfer, gone after.
 */
class DownloadService : Service() {
    private var awake: PowerManager.WakeLock? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("download", "Downloads", NotificationManager.IMPORTANCE_LOW))
        val launch = PendingIntent.getActivity(this, 3, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        startForeground(3, Notification.Builder(this, "download").setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle("LightTouch").setContentText(intent?.getStringExtra("text") ?: "Downloading iOS")
            .setContentIntent(launch).setOngoing(true).build())
        // The screen may turn off during a long download.
        if (awake == null) awake = getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "LightTouch:download").apply { acquire(3 * 60 * 60 * 1000L) }
        return START_NOT_STICKY
    }

    override fun onTimeout(startId: Int, fgsType: Int) = stopSelf()
    override fun onDestroy() { awake?.let { if (it.isHeld) it.release() }; awake = null; super.onDestroy() }
    override fun onBind(intent: Intent): IBinder? = null

    companion object {
        private var holders = 0

        /** Call from the foreground (Android refuses to start a foreground service from the background). */
        fun hold(context: Context, text: String) = synchronized(this) {
            holders++
            context.startForegroundService(Intent(context, DownloadService::class.java).putExtra("text", text))
        }

        fun release(context: Context) = synchronized(this) {
            if (holders > 0 && --holders == 0) context.stopService(Intent(context, DownloadService::class.java))
        }
    }
}

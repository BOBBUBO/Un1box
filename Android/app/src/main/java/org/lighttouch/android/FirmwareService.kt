package org.lighttouch.android

import android.app.*
import android.content.Intent
import android.os.*
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean

/** One userspace preparation per process, independent of a running QEMU worker. */
class FirmwareService : Service() {
    private val consumed = AtomicBoolean(false)
    @Volatile private var destination = ""
    @Volatile private var failure = ""
    @Volatile private var currentFirmwareId = ""
    private val binder = object : IFirmware.Stub() {
        override fun supports(firmwareId: String): Boolean = FirmwareNative.supports(firmwareId)
        override fun start(firmwareId: String, ipswPath: String, trustedToolsRoot: String): Bundle {
            check(consumed.compareAndSet(false, true)) { "A fresh firmware worker is required" }
            foreground()
            currentFirmwareId = firmwareId
            val parent = File(filesDir, "prepared").apply { mkdirs() }
            destination = File(parent, "job-${java.lang.Long.toHexString(SystemClock.uptimeMillis())}").absolutePath
            Thread({
                try {
                    val catalog = File(filesDir, "firmware-catalog.json")
                    if (!catalog.exists()) assets.open("firmware-catalog.json").use { input -> catalog.outputStream().use { input.copyTo(it) } }
                    val root = File(trustedToolsRoot)
                    FirmwareNative.prepare(catalog.absolutePath, firmwareId, ipswPath,
                        File(root, "release-guest-unpacked/guest-tools").absolutePath,
                        File(root, "inventory.json").absolutePath, destination)
                } catch (e: Exception) { failure = e.message ?: "Firmware preparation failed" }
            }, "userspace-firmware").start()
            return status()
        }
        override fun status(): Bundle {
            val values = FirmwareNative.status()
            return Bundle().apply {
                putString("state", if (failure.isNotEmpty()) "failed" else values[0]); putString("stage", values[1])
                putLong("done", values[2].toLong()); putLong("total", values[3].toLong()); putInt("code", values[4].toInt())
                putString("error", if (failure.isNotEmpty()) failure else values[5]); putString("destination", destination)
                putInt("pid", Process.myPid()); putString("firmwareId", currentFirmwareId)
            }
        }
        override fun cancel() { FirmwareNative.cancel() }
        override fun finish() { Handler(mainLooper).postDelayed({ stopForeground(STOP_FOREGROUND_REMOVE); stopSelf(); Process.killProcess(Process.myPid()) }, 200) }
    }
    override fun onBind(intent: Intent): IBinder = binder
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int { foreground(); return START_NOT_STICKY }
    private fun foreground() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("firmware", "Firmware preparation", NotificationManager.IMPORTANCE_LOW))
        val launch = PendingIntent.getActivity(this, 1, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        startForeground(2, Notification.Builder(this, "firmware").setSmallIcon(android.R.drawable.ic_menu_upload)
            .setContentTitle("LightTouch preparation").setContentText("Userspace preparation; return to view progress or cancel")
            .setContentIntent(launch).setOngoing(true).build())
    }
}
internal object FirmwareNative {
    init { System.loadLibrary("lighttouch_firmware_jni") }
    external fun supports(id: String): Boolean
    external fun prepare(catalog: String, id: String, ipsw: String, tools: String, inventory: String, destination: String): Int
    external fun status(): Array<String>
    external fun cancel()
}

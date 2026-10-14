package org.lighttouch.android

import android.app.*
import android.content.Intent
import android.media.*
import android.os.*
import android.view.Surface
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean

/** Same app UID, separate native address space. Every session consumes this process. */
class EngineService : Service() {
    @Volatile private var error = ""
    @Volatile private var userPaused = false
    @Volatile private var focusPaused = false
    private val started = AtomicBoolean(false)
    private val stopping = AtomicBoolean(false)
    private val alive = AtomicBoolean(true)
    @Volatile private var audio: AudioTrack? = null
    @Volatile private var focus: AudioFocusRequest? = null
    private val audioManager by lazy { getSystemService(AudioManager::class.java) }
    private val binder = object : IEngine.Stub() {
        override fun importBase(source: String, destination: String): String = Native.importBase(source, destination)
        override fun start(deviceDirectory: String): Bundle {
            if (!started.compareAndSet(false, true)) return status()
            foreground()
            error = Native.start(deviceDirectory)
            if (error.isEmpty()) requestAudioFocus()
            val response = status()
            if (error.isNotEmpty()) Handler(mainLooper).postDelayed({
                alive.set(false)
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                Process.killProcess(Process.myPid())
            }, 1000)
            return response
        }
        override fun status(): Bundle {
            val s = Native.status()
            return Bundle().apply {
                putString("state", s.getOrElse(0) { "stopped" })
                putString("detail", s.getOrElse(1) { "" })
                putString("error", error)
                putInt("pid", Process.myPid())
            }
        }
        override fun surface(surface: Surface) { Native.surface(surface) }
        override fun clearSurface() { Native.cancelInput(); Native.surface(null) }
        override fun pause(paused: Boolean) {
            userPaused = paused
            if (!paused) focusPaused = false
            Native.cancelInput()
            val result = Native.pause(paused)
            if (result.isNotEmpty()) error = result
            if (paused) { abandonAudioFocus(); audio?.pause(); audio?.flush() }
            else requestAudioFocus()
        }
        override fun stop() {
            if (!stopping.compareAndSet(false, true)) return
            Native.cancelInput()
            error = Native.stop()
            Thread({
                val deadline = SystemClock.elapsedRealtime() + 15_000
                while (SystemClock.elapsedRealtime() < deadline) {
                    val state = Native.status().firstOrNull()
                    if (state == "stopped" || state == "failed") break
                    Thread.sleep(100)
                }
                // Native domain owns dirty/clean state. A timeout never marks clean.
                alive.set(false)
                abandonAudioFocus()
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                Process.killProcess(Process.myPid())
            }, "guest-stop").start()
        }
        override fun button(button: Int, down: Boolean) { Native.button(button, down).also { if (it.isNotEmpty()) error = it } }
        override fun touch(timestampNanos: Long, ids: IntArray, phases: IntArray, coordinates: FloatArray, width: Int, height: Int, rotation: Int) {
            Native.touch(timestampNanos, ids, phases, coordinates, width, height, rotation).also { if (it.isNotEmpty()) error = it }
        }
        override fun cancelInput() { Native.cancelInput() }
        override fun motion(kind: Int, timestampNanos: Long, xyz: FloatArray, rotation: Int) {
            val result = Native.motion(kind, timestampNanos, xyz, rotation)
            if (result.isNotEmpty()) error = result
        }
        override fun screenshot(path: String): String = try {
            val target = File(path).canonicalFile
            require(target.path.startsWith(File(filesDir, "screenshots").canonicalPath + "/"))
            target.parentFile?.mkdirs()
            val bitmap = Native.capture() ?: error("No guest frame available")
            target.outputStream().use { bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it) }
            bitmap.recycle()
            ""
        } catch (e: Exception) { e.message ?: "Screenshot failed" }
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int { foreground(); return START_NOT_STICKY }
    override fun onBind(intent: Intent): IBinder = binder
    override fun onCreate() {
        super.onCreate()
        Thread({
            val samples = ShortArray(8192)
            var format = ""
            while (alive.get()) {
                val count = Native.audio(samples)
                if (count > 0 && focus != null && !focusPaused && !userPaused && !stopping.get()) {
                    val spec = Native.audioInfo()
                    val next = spec.joinToString()
                    if (next != format) {
                        audio?.release()
                        val mask = if (spec[1] == 1) AudioFormat.CHANNEL_OUT_MONO else AudioFormat.CHANNEL_OUT_STEREO
                        audio = AudioTrack.Builder().setAudioAttributes(audioAttributes()).setAudioFormat(
                            AudioFormat.Builder().setSampleRate(spec[0]).setChannelMask(mask).setEncoding(AudioFormat.ENCODING_PCM_16BIT).build()
                        ).setBufferSizeInBytes(maxOf(AudioTrack.getMinBufferSize(spec[0], mask, AudioFormat.ENCODING_PCM_16BIT), count * 4)).build()
                        audio?.play()
                        format = next
                    }
                    audio?.write(samples, 0, count, AudioTrack.WRITE_BLOCKING)
                } else Thread.sleep(10)
            }
            audio?.release()
        }, "guest-audio").start()
    }
    private fun audioAttributes() = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_GAME).setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build()
    private fun requestAudioFocus() {
        if (focus != null) { audio?.play(); return }
        val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN).setAudioAttributes(audioAttributes())
            .setOnAudioFocusChangeListener({ change ->
                if (change < 0) {
                    focusPaused = true
                    Native.cancelInput(); Native.pause(true); audio?.pause(); audio?.flush()
                    if (change == AudioManager.AUDIOFOCUS_LOSS) abandonAudioFocus()
                } else if (change == AudioManager.AUDIOFOCUS_GAIN && focusPaused && !userPaused && !stopping.get()) {
                    focusPaused = false
                    Native.pause(false); audio?.play()
                }
            }, Handler(mainLooper)).build()
        if (audioManager.requestAudioFocus(request) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED) focus = request
    }
    private fun abandonAudioFocus() { focus?.let { audioManager.abandonAudioFocusRequest(it) }; focus = null }
    private fun foreground() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("guest", "Running guest", NotificationManager.IMPORTANCE_LOW))
        val launch = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        startForeground(1, Notification.Builder(this, "guest").setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle("LightTouch guest").setContentText("Tap to return; Stop ends this worker").setContentIntent(launch).setOngoing(true).build())
    }
    override fun onDestroy() { alive.set(false); Native.cancelInput(); abandonAudioFocus(); super.onDestroy() }
}

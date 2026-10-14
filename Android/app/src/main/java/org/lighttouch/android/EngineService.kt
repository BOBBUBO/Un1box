package org.lighttouch.android

import android.app.*
import android.content.Intent
import android.media.*
import android.os.*
import android.view.Surface
import java.io.File
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicBoolean

/** Same app UID, separate native address space. Every session consumes this process. */
class EngineService : Service() {
    @Volatile private var error = ""
    @Volatile private var userPaused = false
    @Volatile private var focusPaused = false
    private val started = AtomicBoolean(false)
    private val failureRecorded = AtomicBoolean(false)
    private val stopping = AtomicBoolean(false)
    private val alive = AtomicBoolean(true)
    @Volatile private var audio: AudioTrack? = null
    @Volatile private var focus: AudioFocusRequest? = null
    private val touchAdmissionGuard = Any()
    private val touchTimestampOrder = TouchTimestampOrder()
    private val audioGuard = Any()
    private val audioObservations = AudioObservations()
    @Volatile private var audioError = ""
    private val audioManager by lazy { getSystemService(AudioManager::class.java) }
    private val buttonDownAdmissions = java.util.concurrent.atomic.AtomicLongArray(2)
    private val buttonUpAdmissions = java.util.concurrent.atomic.AtomicLongArray(2)
    private val buttonRejections = java.util.concurrent.atomic.AtomicLong(0)
    private val shutdownWatchdog = ShutdownWatchdog(BuildConfig.SHUTDOWN_GRACE_MS, SystemClock::elapsedRealtime,
        requestNativeStop = { Native.stop() }, cancelNativeContacts = { Native.cancelInput() },
        nativeStatus = { Native.status() }, dispose = { receipt -> disposeAfterShutdown(receipt) })
    private val binder = object : IEngine.Stub() {
        private fun requireDebugDiagnosis() { check(applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE != 0) { "Debug diagnosis is unavailable in this build" } }
        override fun debugArmQmp(): String { requireDebugDiagnosis(); return Native.debugArmQmp() }
        override fun debugEnableTouchTrace(): String {
            requireDebugDiagnosis()
            if (started.get()) return "Touch trace selection requires a fresh unused worker"
            return Native.debugEnableTouchTrace()
        }
        override fun debugSelectGraphicsProvider(choice: Int): String {
            requireDebugDiagnosis()
            if (started.get()) return "Graphics provider selection requires a fresh unused worker"
            return Native.debugSelectGraphicsProvider(choice)
        }
        override fun debugQmpSnapshot(): Bundle {
            requireDebugDiagnosis()
            val result = Native.debugQmpSnapshot()
            return Bundle().apply { putString("error", result[0]); putString("path", result[1]) }
        }
        override fun importBase(source: String, displayName: String): String = Native.importBase(source, File(filesDir, "library").absolutePath,
            displayName, File(filesDir, "bootrom.bin").absolutePath, File(filesDir, "guest.itpack").absolutePath)
        override fun importPrepared(source: String, displayName: String): String = Native.importBase(source, File(filesDir, "library").absolutePath, displayName, File(filesDir, "bootrom.bin").absolutePath, "")
        override fun records(): Array<Bundle> = Native.records(File(filesDir, "library").absolutePath).mapNotNull { row ->
            if (row[0].isEmpty()) { error = row.getOrElse(1) { "Device library unavailable" }; null }
            else Bundle().apply {
                putString("id", row[0]); putString("name", row[1]); putString("board", row[2]); putString("build", row[3])
                putBoolean("selected", row[4] == "1"); putBoolean("dirty", row[5] == "1")
                putBoolean("bootromAvailable", row.getOrElse(9) { "0" } == "1"); putBoolean("guestPackageAvailable", row.getOrElse(10) { "0" } == "1")
                putString("contacts", row[6]); putBoolean("gyro", row[7] == "1"); putBoolean("acceleration", row[8] == "1")
            }
        }.toTypedArray()
        override fun select(recordId: String): String = Native.select(File(filesDir, "library").absolutePath, recordId)
        override fun start(recordId: String): Bundle = startRecord(recordId, coldRecovery = false)
        override fun recoverStart(recordId: String): Bundle = startRecord(recordId, coldRecovery = true)
        private fun startRecord(recordId: String, coldRecovery: Boolean): Bundle {
            if (stopping.get()) return Bundle().apply {
                putString("state", "stopping"); putString("error", "This worker is disposing; recovery and Start require a fresh worker.")
            }
            if (!started.compareAndSet(false, true)) return status().apply {
                putString("error", "Start and recovery require a fresh unused guest worker. Stop this worker first.")
            }
            foreground()
            val libraryRoot = File(filesDir, "library").absolutePath
            error = if (coldRecovery) Native.recoverStart(libraryRoot, recordId) else Native.start(libraryRoot, recordId)
            if (error.isEmpty()) { File(filesDir, "worker-stop-error.txt").delete(); if (!userPaused && !stopping.get()) requestAudioFocus() }
            val response = status()
            if (error.isNotEmpty()) Handler(mainLooper).postDelayed({
                persistStopError(error)
                persistObservations("start-failed")
                alive.set(false)
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                Process.killProcess(Process.myPid())
            }, 1000)
            return response
        }
        override fun status(): Bundle {
            val s = Native.status()
            s.getOrNull(2)?.takeIf { it.isNotEmpty() }?.let { error = it }
            if (s.firstOrNull() == "failed" && started.get() && failureRecorded.compareAndSet(false, true)) {
                val retainedError = error.ifEmpty { "Guest worker failed; writable state and diagnostics retained. Start a fresh worker." }
                Thread({ persistStopError(retainedError); persistObservations("unexpected-engine-exit") }, "guest-failure-record").start()
            }
            return Bundle().apply {
                putString("state", s.getOrElse(0) { "stopped" })
                putString("detail", s.getOrElse(1) { "" })
                putString("engineLog", s.getOrElse(3) { "" })
                putString("debugQmpPath", s.getOrElse(4) { "" })
                putString("graphicsProviderChoice", s.getOrElse(5) { "platform" })
                putBoolean("graphicsProviderFixed", s.getOrElse(6) { "0" } == "1")
                putBoolean("touchTraceConfigured", s.getOrElse(7) { "0" } == "1")
                putString("error", error.ifEmpty { audioError })
                putInt("pid", Process.myPid())
                putInt("uid", Process.myUid())
                putBoolean("sessionStarted", started.get())
                putBundle("observations", observations())
            }
        }
        override fun surface(surface: Surface) {
            // The :engine Binder parcel is a worker-owned Java reference. Native
            // retains its own ANativeWindow ref; release this copy promptly so an
            // old CPU-connected Surface is destroyed when Native replaces it.
            try { Native.surface(surface) } finally { surface.release() }
        }
        override fun clearSurface() { Native.cancelInput(); Native.surface(null) }
        override fun pause(paused: Boolean) {
            // A background callback can race the stop button. Shutdown already
            // cancels input; an inactive pause must not replace its diagnostic.
            if (stopping.get()) return
            userPaused = paused
            if (!paused) focusPaused = false
            Native.cancelInput()
            val result = Native.pause(paused)
            if (result.isNotEmpty()) error = result
            synchronized(audioGuard) { if (paused) audioObservations.pauses++ else audioObservations.resumes++ }
            if (paused) { abandonAudioFocus(); pauseAudio() }
            else requestAudioFocus()
        }
        override fun stop() {
            if (!stopping.compareAndSet(false, true)) return
            shutdownWatchdog.request()
        }
        override fun button(button: Int, down: Boolean) {
            val result = Native.button(button, down)
            if (result.isNotEmpty()) { error = result; buttonRejections.incrementAndGet() }
            else if (button in 0..1) (if (down) buttonDownAdmissions else buttonUpAdmissions).incrementAndGet(button)
        }
        override fun touch(timestampNanos: Long, ids: IntArray, phases: IntArray, coordinates: FloatArray, width: Int, height: Int, rotation: Int): String {
            // Keep selection and native admission in the same worker-owned order.
            // Cancel/pause/surface replacement deliberately do not reset this clock.
            return synchronized(touchAdmissionGuard) {
                val ordered = try { touchTimestampOrder.select(timestampNanos) }
                catch (failure: IllegalArgumentException) {
                    val rejected = "${failure.message}; gesture cancelled. Start a new gesture."
                    error = rejected
                    Native.cancelInput()
                    return@synchronized rejected
                }
                val result = Native.touch(ordered, ids, phases, coordinates, width, height, rotation)
                if (result.isNotEmpty()) error = result
                result
            }
        }
        override fun cancelInput() { Native.cancelInput() }
        override fun motion(kind: Int, timestampNanos: Long, xyz: FloatArray, rotation: Int) {
            val result = Native.motion(kind, timestampNanos, xyz, rotation)
            if (result.isNotEmpty()) error = result
        }
        override fun guestRequest(operation: Int, argument: String, timeoutMs: Int): Bundle {
            val result = Native.guestRequest(operation, argument, timeoutMs)
            return Bundle().apply { putLong("requestId", result[0].toLong()); putString("error", result[1]) }
        }
        override fun guestPoll(requestId: Long): Bundle {
            val result = Native.guestPoll(requestId)
            return Bundle().apply { putInt("state", result[0].toInt()); putInt("guestStatus", result[1].toInt()); putString("output", result[2]); putString("error", result[3]) }
        }
        override fun guestCancel(requestId: Long) { Native.guestCancel(requestId) }
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
    @Suppress("DEPRECATION")
    private fun persistObservations(reason: String) {
        fun encode(bundle: Bundle): JSONObject = JSONObject().apply {
            for (key in bundle.keySet()) when (val value = bundle.get(key)) { is Bundle -> put(key, encode(value)); else -> put(key, value) }
        }
        try {
            val receipt = encode(binder.status()).put("reason", reason).put("hostElapsedMs", SystemClock.elapsedRealtime())
                .put("audiblePlaybackVerified", false).put("visibleGuestVerified", false)
            File(filesDir, "worker-observations-${Process.myPid()}.json").writeText(receipt.toString(2))
        } catch (_: Exception) { /* State markers and per-worker native logs remain authoritative. */ }
    }
    private fun persistStopError(message: String) {
        try { File(filesDir, "worker-stop-error-${Process.myPid()}.txt").writeText(message) } catch (_: java.io.IOException) { /* State markers remain authoritative. */ }
        try { File(filesDir, "worker-stop-error.txt").writeText(message) } catch (_: java.io.IOException) { /* Legacy private diagnostic only, not another worker's live status. */ }
    }
    private fun disposeAfterShutdown(receipt: ShutdownReceipt) {
        alive.set(false)
        val message = when {
            receipt.reason == "deadline-unconfirmed" -> "Guest shutdown was not confirmed within ${BuildConfig.SHUTDOWN_GRACE_MS / 1000} seconds; worker disposed and writable state retained. ${receipt.progress.error}"
            receipt.terminalState == "failed" -> "Guest worker reported failure; writable state retained: ${receipt.progress.error}"
            else -> receipt.progress.error
        }.take(4096)
        error = message
        disposeWithoutWaitingForReceipt(writeReceipt = {
            if (message.isNotEmpty()) persistStopError(message)
            // Cached facts only. Native.status/observations and audio/framework
            // cleanup can block; none may precede this process disposal.
            val record = JSONObject().put("reason", receipt.reason).put("admissionStage", receipt.progress.stage)
                .put("error", message).put("nativeTerminalState", receipt.terminalState ?: JSONObject.NULL)
                .put("acceptedAtHostMs", receipt.acceptedAtMs).put("deadlineHostMs", receipt.deadlineMs).put("observedAtHostMs", receipt.observedAtMs)
                .put("workerPid", Process.myPid()).put("shutdownConfirmationInferred", false).put("nativeStateMarkersModified", false)
                .put("receiptPersistenceBestEffort", true)
            try { File(filesDir, "worker-shutdown-${Process.myPid()}.json").writeText(record.toString(2)) }
            catch (_: java.io.IOException) { /* Retained state markers stay authoritative. */ }
        }, terminate = { Process.killProcess(Process.myPid()) })
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int { foreground(); return START_NOT_STICKY }
    override fun onBind(intent: Intent): IBinder = binder
    override fun onCreate() {
        super.onCreate()
        // A fresh worker must not inherit a prior PID's error as current status.
        // The UI reads the exited PID's receipt/error; state markers and legacy
        // diagnostics remain available for cold recovery review.
        Thread({
            val samples = ShortArray(8192)
            var format = ""
            while (alive.get()) {
                val count = Native.audio(samples)
                if (count < 0) {
                    synchronized(audioGuard) { audioObservations.nativePullFailures++ }
                    audioError = "Guest audio could not be read (status ${-count}). Stop and restart the guest."
                    Thread.sleep(100)
                    continue
                }
                if (count > 0) synchronized(audioGuard) { audioObservations.samples(samples, 0, count, false) }
                if (count > 0 && focus != null && !focusPaused && !userPaused && !stopping.get()) {
                    try {
                        val spec = Native.audioInfo()
                        require(spec[0] > 0 && spec[1] in 1..2) { "Invalid guest audio format" }
                        val next = spec.joinToString()
                        synchronized(audioGuard) {
                            if (next != format) {
                                audio?.let { audioObservations.observeTrack(it.playbackHeadPosition, it.underrunCount); it.release() }
                                val mask = if (spec[1] == 1) AudioFormat.CHANNEL_OUT_MONO else AudioFormat.CHANNEL_OUT_STEREO
                                val minimum = AudioTrack.getMinBufferSize(spec[0], mask, AudioFormat.ENCODING_PCM_16BIT)
                                require(minimum > 0) { "Guest audio format unavailable" }
                                audio = AudioTrack.Builder().setAudioAttributes(audioAttributes()).setAudioFormat(
                                    AudioFormat.Builder().setSampleRate(spec[0]).setChannelMask(mask).setEncoding(AudioFormat.ENCODING_PCM_16BIT).build()
                                ).setBufferSizeInBytes(maxOf(minimum, count * 4)).build()
                                check(audio!!.state == AudioTrack.STATE_INITIALIZED) { "Audio output did not initialize" }
                                audioObservations.trackCreated(spec[0], spec[1]); audio!!.play(); format = next
                            }
                            var offset = 0
                            // A positive short write retains the unwritten tail. Stop,
                            // pause and focus loss are checked before each retry.
                            while (offset < count && alive.get() && focus != null && !focusPaused && !userPaused && !stopping.get()) {
                                val requested = count - offset
                                val written = audio!!.write(samples, offset, requested, AudioTrack.WRITE_BLOCKING)
                                audioObservations.writeCalls++; audioObservations.lastWriteCode = written
                                if (written <= 0) {
                                    audioObservations.writeFailures++
                                    audioError = "Guest audio output stopped (code $written). Stop and restart if it does not recover."
                                    audio?.release(); audio = null; format = ""
                                    break
                                }
                                if (written < requested) audioObservations.shortWrites++
                                audioObservations.samples(samples, offset, written, true); offset += written; audioError = ""
                            }
                            audio?.let { audioObservations.observeTrack(it.playbackHeadPosition, it.underrunCount) }
                        }
                    } catch (failure: Exception) {
                        synchronized(audioGuard) { audioObservations.writeFailures++; audio?.release(); audio = null; format = "" }
                        audioError = "Guest audio output failed: ${failure.message ?: "unknown error"}. Stop and restart the guest."
                        Thread.sleep(100)
                    }
                } else Thread.sleep(10)
            }
            synchronized(audioGuard) { audio?.release(); audio = null }
        }, "guest-audio").start()
    }
    private fun pauseAudio() = synchronized(audioGuard) {
        audio?.let { audioObservations.observeTrack(it.playbackHeadPosition, it.underrunCount); it.pause(); it.flush(); audioObservations.flushed() }
    }
    private fun observations() = Bundle().apply {
        val values = Native.observations()
        val keys = listOf("hostMonotonicNs", "presentedFrames", "firstPresentHostNs", "lastPresentHostNs", "maxPresentGapNs", "maxPresentWorkNs",
            "windowFailures", "frameCopyFailures", "surfaceGeneration", "guestFrameSequence", "guestFrameTimestampNs", "nativePcmFailures", "pcmDroppedFrames",
            "presentIntervalsTo16ms", "presentIntervalsTo33ms", "presentIntervalsTo50ms", "presentIntervalsTo100ms", "presentIntervalsTo250ms", "presentIntervalsOver250ms", "producedFrames", "producerFrameSequence", "producerFrameTimestampNs",
            "framePublishedHostNs", "pcmCapturedFrames", "pcmPulledFrames", "sharedPcmDroppedFrames", "inputRequests", "pcmQueuedPackets", "firstWindowLockError", "lastWindowLockError", "firstWindowPostError", "lastWindowPostError", "lastWindowGeometryError",
            "windowGeometryFailures", "windowBufferFormat", "windowBufferWidth", "windowBufferHeight", "windowBufferStride", "invalidWindowBuffers", "firstWindowErrorHostNs", "runtimeDiagnosticsStatus", "engineReady", "displaySleeping", "backlightLevel",
            "guestAgentSampleStatus", "guestAgentState", "shutdownPhase", "shutdownRoute", "shutdownReplyStatus", "shutdownReplyReceived", "guestFrameWidth", "guestFrameHeight", "guestFrameSamplePixels", "guestFrameNonzeroRgbSamples", "guestFrameRgbSampleSum",
            "windowAttached", "presentationEpoch", "presenterPhase", "presenterPhaseEntryHostNs", "presenterPhaseGeneration", "presenterPhaseEpoch", "presenterPhaseRevision", "presenterPhaseSampleStable")
        for ((index, key) in keys.withIndex()) putLong(key, values[index])
        putBoolean("presenterTelemetryGuestEvidence", false)
        val diagnosticAvailable = getLong("runtimeDiagnosticsStatus") == 0L
        val agentAvailable = getLong("guestAgentSampleStatus") == 0L
        putBoolean("runtimeDiagnosticsAvailable", diagnosticAvailable)
        putBoolean("guestAgentStateAvailable", agentAvailable)
        putBoolean("guestAgentEndpointAvailable", false)
        putBoolean("guestAgentFreshnessAgeAvailable", false)
        putString("guestAgentStateLabel", when {
            !agentAvailable -> "unavailable"
            !diagnosticAvailable || getLong("engineReady") == 0L -> "not ready"
            getLong("guestAgentState") == 1L -> "alive"
            getLong("guestAgentState") == 2L -> "stale"
            else -> "absent"
        })
        putBundle("audio", synchronized(audioGuard) { audioObservations.snapshot() })
        putBoolean("userPaused", userPaused); putBoolean("focusPaused", focusPaused); putBoolean("audioFocusHeld", focus != null)
        putLongArray("buttonDownAdmissions", LongArray(2) { buttonDownAdmissions.get(it) })
        putLongArray("buttonUpAdmissions", LongArray(2) { buttonUpAdmissions.get(it) })
        putLong("buttonRejections", buttonRejections.get())
        // Volatile immutable snapshot: observers never wait on touch/native admission.
        val touchClock = touchTimestampOrder.snapshot
        putBundle("touchTimestampSelection", Bundle().apply {
            putLong("rawUptimeNs", touchClock.rawUptimeNs); putLong("orderedUptimeNs", touchClock.orderedUptimeNs)
            putLong("selections", touchClock.selections); putLong("adjustedSelections", touchClock.adjustedSelections)
            putLong("rejectedSelections", touchClock.rejectedSelections); putBoolean("guestConsumptionInferred", false)
        })
    }
    private fun audioAttributes() = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_GAME).setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build()
    private fun requestAudioFocus() {
        if (focus != null) { synchronized(audioGuard) { audio?.play() }; return }
        val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN).setAudioAttributes(audioAttributes())
            .setOnAudioFocusChangeListener({ change ->
                if (change < 0) {
                    focusPaused = true
                    synchronized(audioGuard) { audioObservations.focusLosses++ }
                    Native.cancelInput(); Native.pause(true); pauseAudio()
                    if (change == AudioManager.AUDIOFOCUS_LOSS) abandonAudioFocus()
                } else if (change == AudioManager.AUDIOFOCUS_GAIN && focusPaused && !userPaused && !stopping.get()) {
                    focusPaused = false
                    synchronized(audioGuard) { audioObservations.focusGains++ }
                    Native.pause(false); synchronized(audioGuard) { audio?.play() }
                }
            }, Handler(mainLooper)).build()
        if (audioManager.requestAudioFocus(request) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED) focus = request
        else {
            synchronized(audioGuard) { audioObservations.focusDenied++ }
            focusPaused = true; Native.cancelInput(); Native.pause(true)
            audioError = "Audio focus unavailable. Pause and resume after other audio stops."
        }
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

package org.lighttouch.android

import java.io.File

enum class QueueState { PENDING, DOWNLOADING, READY, INSTALLING, DONE, FAILED }

/** One app in the install queue, as the screen and the notification show it. */
data class QueueItem(val id: Long, val key: String, val name: String, val state: QueueState = QueueState.PENDING,
                     val phase: String = "Waiting", val progress: Float? = null, val error: String? = null) {
    /** Still has work to do: it keeps the guest running and the service alive. */
    val active get() = state != QueueState.DONE && state != QueueState.FAILED
    val cancellable get() = state == QueueState.PENDING || state == QueueState.DOWNLOADING || state == QueueState.READY
}

/** What a running step reports back to its item. */
interface QueueReport {
    fun update(phase: String, fraction: Float? = null)
    fun rename(name: String)
}

/** The two steps of an install. Both throw with a message fit to show the user. */
interface QueueWork<P> {
    /** Download (or copy) the app's file; stop with an exception once [cancelled] is true. */
    fun fetch(payload: P, report: QueueReport, cancelled: () -> Boolean): File
    /** Install the fetched file into the guest. */
    fun install(payload: P, file: File, report: QueueReport)
    /** The fetched file is no longer needed (installed, failed or cancelled). */
    fun discard(file: File)
}

/**
 * The Legacy Store's install queue, with no Android in it. Items are fetched one at a time and installed one
 * at a time, in the order they were added; a fetch may overlap the install before it, but only one fetched file
 * waits at once (an IPA can be hundreds of megabytes). [spawn] starts a step's thread; tests run it inline.
 * [onBusy] and [onIdle] fire when the queue gains its first unfinished item and when it loses its last, never
 * under the lock. [onChange] fires after every change.
 */
class InstallQueue<P>(private val work: QueueWork<P>,
                      private val spawn: (String, () -> Unit) -> Unit = { name, body -> Thread(body, name).apply { isDaemon = true }.start() }) {
    private class Entry<P>(var item: QueueItem, val payload: P) { var file: File? = null; @Volatile var cancelled = false }

    private val lock = Any()
    private val announce = Any()
    private val entries = mutableListOf<Entry<P>>()
    private var nextId = 1L
    private var fetching = false
    private var installing = false

    @Volatile var onChange: () -> Unit = {}
    @Volatile var onBusy: () -> Unit = {}
    @Volatile var onIdle: () -> Unit = {}

    fun snapshot(): List<QueueItem> = synchronized(lock) { entries.map { it.item } }
    val active get() = synchronized(lock) { entries.any { it.item.active } }

    /** False when the same app is already waiting or running; a finished attempt at it is replaced. */
    fun enqueue(key: String, name: String, payload: P): Boolean {
        val accepted = synchronized(lock) {
            if (entries.any { it.item.key == key && it.item.active }) return@synchronized false
            entries.removeAll { it.item.key == key }
            entries += Entry(QueueItem(nextId++, key, name), payload)
            true
        }
        if (accepted) changed()
        return accepted
    }

    /** Pending and fetched items leave the queue; a download in progress stops and leaves. An install can't be taken back. */
    fun cancel(id: Long) {
        var discard: File? = null
        synchronized(lock) {
            val entry = entries.firstOrNull { it.item.id == id } ?: return@synchronized
            when (entry.item.state) {
                QueueState.PENDING -> entries.remove(entry)
                QueueState.READY -> { discard = entry.file; entry.file = null; entries.remove(entry) }
                QueueState.DOWNLOADING -> { entry.cancelled = true; entry.item = entry.item.copy(phase = "Cancelling") }
                else -> {}
            }
        }
        discard?.let(work::discard)
        changed()
    }

    fun retry(id: Long) {
        val retried = synchronized(lock) {
            val index = entries.indexOfFirst { it.item.id == id && it.item.state == QueueState.FAILED }
            if (index < 0) return@synchronized false
            // Back of the line, like any newly added app.
            val entry = entries.removeAt(index)
            entry.cancelled = false
            entry.item = entry.item.copy(state = QueueState.PENDING, phase = "Waiting", progress = null, error = null)
            entries += entry
            true
        }
        if (retried) changed()
    }

    fun dismiss(id: Long) {
        synchronized(lock) { entries.removeAll { it.item.id == id && !it.item.active } }
        changed()
    }

    fun clearFinished() {
        synchronized(lock) { entries.removeAll { !it.item.active } }
        changed()
    }

    /** Announce a change, then start whatever step can now run. */
    private fun changed() {
        // Decided under the queue's lock but announced outside it, and in the order decided: a busy that overtook
        // the idle before it would have the service released while the queue still has work.
        synchronized(announce) {
            val transition = synchronized(lock) { transition(entries.any { it.item.active }) }
            if (transition > 0) onBusy()
            onChange()
            if (transition < 0) onIdle()
        }
        pump()
    }

    private var announced = false
    /** 1 when the queue just became busy, -1 when it just became idle, 0 otherwise. */
    private fun transition(active: Boolean): Int = when {
        active && !announced -> { announced = true; 1 }
        !active && announced -> { announced = false; -1 }
        else -> 0
    }

    private fun update(entry: Entry<P>, change: (QueueItem) -> QueueItem) {
        synchronized(lock) { entry.item = change(entry.item) }
        onChange()
    }

    private fun reporter(entry: Entry<P>) = object : QueueReport {
        override fun update(phase: String, fraction: Float?) = update(entry) { it.copy(phase = phase, progress = fraction) }
        override fun rename(name: String) = update(entry) { it.copy(name = name) }
    }

    /** Start whichever steps can run now. */
    private fun pump() {
        var fetch: Entry<P>? = null
        var install: Entry<P>? = null
        synchronized(lock) {
            if (!installing) entries.firstOrNull { it.item.state == QueueState.READY }?.let { entry ->
                installing = true; install = entry
                entry.item = entry.item.copy(state = QueueState.INSTALLING, phase = "Installing", progress = null)
            }
            // One fetched file waits at most; the install before it may still be running.
            if (!fetching && entries.none { it.item.state == QueueState.READY }) entries.firstOrNull { it.item.state == QueueState.PENDING }?.let { entry ->
                fetching = true; fetch = entry
                entry.item = entry.item.copy(state = QueueState.DOWNLOADING, phase = "Downloading", progress = 0f)
            }
        }
        if (fetch != null || install != null) onChange()
        install?.let { entry -> spawn("store-install") { runInstall(entry) } }
        fetch?.let { entry -> spawn("store-download") { runFetch(entry) } }
    }

    private fun runFetch(entry: Entry<P>) {
        var file: File? = null
        var failure: String? = null
        try { file = work.fetch(entry.payload, reporter(entry)) { entry.cancelled } }
        catch (e: Exception) { failure = e.message ?: "Download failed" }
        var discard: File? = null
        synchronized(lock) {
            fetching = false
            when {
                entry.cancelled -> { discard = file; entries.remove(entry) }
                file != null -> { entry.file = file; entry.item = entry.item.copy(state = QueueState.READY, phase = "Waiting to install", progress = null) }
                else -> entry.item = entry.item.copy(state = QueueState.FAILED, phase = "", progress = null, error = failure)
            }
        }
        discard?.let(work::discard)
        changed()
    }

    private fun runInstall(entry: Entry<P>) {
        val file = synchronized(lock) { entry.file.also { entry.file = null } }
        var failure: String? = null
        try { work.install(entry.payload, requireNotNull(file), reporter(entry)) } catch (e: Exception) { failure = e.message ?: "Install failed" }
        file?.let(work::discard)
        synchronized(lock) {
            installing = false
            entry.item = if (failure == null) entry.item.copy(state = QueueState.DONE, phase = "Installed. Find it on the Home screen.", progress = 1f)
            else entry.item.copy(state = QueueState.FAILED, phase = "", progress = null, error = failure)
        }
        changed()
    }
}

package org.lighttouch.android

import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

internal data class ShutdownProgress(val stage: String, val error: String = "")
/** Only the appended fields from the same native poll may establish clean shutdown. */
internal data class ShutdownNativeSnapshot(val valid: Boolean, val confirmed: Boolean,
    val dirty: Boolean?, val storageFailed: Boolean?, val engineExit: Int?) {
    fun clean(state: String?) = valid && state == "stopped" && confirmed && dirty == false && storageFailed == false && engineExit == 0
    companion object {
        fun from(status: Array<String>): ShutdownNativeSnapshot {
            fun flag(index: Int): Boolean? = when (status.getOrNull(index)) { "0" -> false; "1" -> true; else -> null }
            val confirmed = flag(9); val dirty = flag(10); val storage = flag(11); val exit = status.getOrNull(12)?.toIntOrNull()
            return ShutdownNativeSnapshot(flag(8) == true && confirmed != null && dirty != null && storage != null && exit != null,
                confirmed == true, dirty, storage, exit)
        }
    }
}
internal data class ShutdownReceipt(val reason: String, val progress: ShutdownProgress, val terminalState: String?,
    val acceptedAtMs: Long, val deadlineMs: Long, val observedAtMs: Long,
    val nativeSnapshot: ShutdownNativeSnapshot? = null) {
    val cleanConfirmed: Boolean get() = nativeSnapshot?.clean(terminalState) == true
}

/** Receipt persistence is best effort; filesystem waits cannot postpone disposal. */
internal fun disposeWithoutWaitingForReceipt(writeReceipt: () -> Unit, terminate: () -> Unit) {
    try {
        Thread({
            try { writeReceipt() } catch (_: Exception) { /* No confirmation is inferred from missing receipt bytes. */ }
        }, "guest-stop-receipt").apply { isDaemon = true }.start()
    } finally { terminate() }
}

/** The deadline never invokes native code and never waits for the admission thread. */
internal class ShutdownWatchdog(
    private val graceMs: Long,
    private val now: () -> Long,
    private val requestNativeStop: () -> String,
    private val cancelNativeContacts: () -> Unit,
    private val nativeStatus: () -> Array<String>,
    private val dispose: (ShutdownReceipt) -> Unit,
    private val persistTerminal: (ShutdownReceipt) -> Unit = {}
) {
    private val requested = AtomicBoolean(false)
    private val disposed = AtomicBoolean(false)
    private val progress = AtomicReference(ShutdownProgress("not-requested"))
    private val timer = Executors.newSingleThreadScheduledExecutor { task -> Thread(task, "guest-stop-deadline").apply { isDaemon = true } }
    private var acceptedAt = 0L
    private var deadline = 0L

    fun request(): Boolean {
        if (!requested.compareAndSet(false, true)) return false
        require(graceMs > 0)
        acceptedAt = now(); deadline = acceptedAt + graceMs
        progress.set(ShutdownProgress("shutdown-admission"))
        // This must happen before the worker can initialize or call Native.
        timer.schedule({ finish("deadline-unconfirmed", null) }, graceMs, TimeUnit.MILLISECONDS)
        Thread({
            try {
                if (disposed.get()) return@Thread
                val admissionError = requestNativeStop().take(4096)
                if (disposed.get()) return@Thread
                progress.set(ShutdownProgress("contact-cancellation", admissionError))
                cancelNativeContacts()
                if (disposed.get()) return@Thread
                progress.set(ShutdownProgress("terminal-poll", admissionError))
                while (!disposed.get()) {
                    val state = nativeStatus()
                    if (disposed.get()) return@Thread
                    if (now() >= deadline) { finish("deadline-unconfirmed", null); return@Thread }
                    if (state.firstOrNull() in listOf("stopped", "failed")) {
                        val reportedError = if (state.firstOrNull() == "failed") {
                            state.getOrNull(2)?.takeIf { it.isNotEmpty() }
                                ?: admissionError.takeIf { it.isNotEmpty() }
                                ?: state.getOrElse(1) { "Shutdown was not confirmed" }
                        } else admissionError
                        progress.set(ShutdownProgress("native-terminal", reportedError.take(4096)))
                        val terminal = ShutdownReceipt("native-terminal", progress.get(), state.firstOrNull(),
                            acceptedAt, deadline, now(), ShutdownNativeSnapshot.from(state))
                        // The timer remains armed and disposed remains false during IO.
                        // A blocked writer can never prevent deadline disposal.
                        progress.set(ShutdownProgress("terminal-receipt", reportedError.take(4096)))
                        persistTerminal(terminal)
                        if (now() >= deadline) { finish("deadline-unconfirmed", null); return@Thread }
                        finish(terminal); return@Thread
                    }
                    Thread.sleep(100)
                }
            } catch (failure: Exception) {
                // Admission/poll failure is not proof of guest termination.
                val stage = if (progress.get().stage == "terminal-receipt") "terminal-receipt-failed" else "native-call-failed"
                progress.set(ShutdownProgress(stage, failure.toString().take(4096)))
            }
        }, "guest-stop-admission").apply { isDaemon = true }.start()
        return true
    }

    private fun finish(reason: String, state: String?) {
        finish(ShutdownReceipt(reason, progress.get(), state, acceptedAt, deadline, now()))
    }
    private fun finish(receipt: ShutdownReceipt) {
        if (!disposed.compareAndSet(false, true)) return
        try { dispose(receipt) } finally { timer.shutdownNow() }
    }
}

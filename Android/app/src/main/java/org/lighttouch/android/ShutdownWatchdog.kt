package org.lighttouch.android

import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

internal data class ShutdownProgress(val stage: String, val error: String = "")
internal data class ShutdownReceipt(val reason: String, val progress: ShutdownProgress, val terminalState: String?,
    val acceptedAtMs: Long, val deadlineMs: Long, val observedAtMs: Long)

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
    private val dispose: (ShutdownReceipt) -> Unit
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
                        finish("native-terminal", state.firstOrNull()); return@Thread
                    }
                    Thread.sleep(100)
                }
            } catch (failure: Exception) {
                // Admission/poll failure is not proof of guest termination.
                progress.set(ShutdownProgress("native-call-failed", failure.toString().take(4096)))
            }
        }, "guest-stop-admission").apply { isDaemon = true }.start()
        return true
    }

    private fun finish(reason: String, state: String?) {
        if (!disposed.compareAndSet(false, true)) return
        val receipt = ShutdownReceipt(reason, progress.get(), state, acceptedAt, deadline, now())
        try { dispose(receipt) } finally { timer.shutdownNow() }
    }
}

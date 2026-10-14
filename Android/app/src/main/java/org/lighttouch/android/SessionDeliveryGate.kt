package org.lighttouch.android

/** Main-thread delivery gate for asynchronous IO belonging to a retired worker.
 * Advance before a bind/start/retirement or Activity destruction. No file read or
 * native operation is performed here; delayed IO posts back to the owner thread.
 */
internal class SessionDeliveryGate {
    private var generation = 0L
    fun advance(): Long = ++generation
    fun deliver(expected: Long, action: () -> Unit): Boolean {
        if (expected != generation) return false
        action()
        return true
    }
}

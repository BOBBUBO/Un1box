package org.lighttouch.android

import java.util.IdentityHashMap

/** Pure UI callback policy; methods never call Binder or platform code. */
internal class ActiveDisplayOwner {
    private class Visibility(var started: Boolean = false, var resumed: Boolean = false, var focused: Boolean = false)
    private val visible = IdentityHashMap<Any, Visibility>()
    private var owner: Any? = null
    private var generation = 0L
    private fun ticket(value: Any): Long = if (owner === value) generation else 0L
    private fun promote(value: Any, state: Visibility): Long {
        if (state.started && state.resumed && state.focused && owner !== value) {
            check(generation < Long.MAX_VALUE); generation++; owner = value
        }
        return ticket(value)
    }
    @Synchronized fun started(value: Any) { visible.getOrPut(value) { Visibility() }.started = true }
    @Synchronized fun resumed(value: Any, actualFocus: Boolean): Long {
        val state = visible.getOrPut(value) { Visibility() }
        state.resumed = true; state.focused = actualFocus
        return promote(value, state)
    }
    @Synchronized fun focusChanged(value: Any, actualFocus: Boolean): Long {
        val state = visible[value] ?: return 0L
        state.focused = actualFocus
        return promote(value, state)
    }
    @Synchronized fun paused(value: Any) { visible[value]?.let { it.resumed = false; it.focused = false } }
    @Synchronized fun owns(value: Any, ticket: Long): Boolean = ticket > 0 && owner === value && ticket == generation
    @Synchronized fun canClaim(value: Any, ticket: Long): Boolean {
        val state = visible[value] ?: return false
        return owns(value, ticket) && state.started && state.resumed && state.focused
    }
    @Synchronized fun stopped(value: Any, ticket: Long): Boolean {
        visible.remove(value)
        if (!owns(value, ticket)) return false
        owner = null
        return true
    }
}

internal object DisplayOwners { val active = ActiveDisplayOwner() }

/** The numeric token is never reused via a newly bound worker closure. */
internal class WorkerDisplayLease {
    private var worker: Any? = null
    private var token = 0L
    private var ownerTicket = 0L
    fun bind(identity: Any, value: Long, ticket: Long = 1L) { require(value > 0 && ticket > 0); worker = identity; token = value; ownerTicket = ticket }
    fun forWorker(identity: Any): Long = if (worker === identity) token else 0L
    fun forOwner(identity: Any, ticket: Long): Long = if (ownerTicket == ticket) forWorker(identity) else 0L
    fun clear() { worker = null; token = 0L; ownerTicket = 0L }
}

/** Serialize pause/focus state with native pause admission, never window calls. */
internal class WorkerLifecycleAdmission {
    @Synchronized fun <T> run(action: () -> T): T = action()
}

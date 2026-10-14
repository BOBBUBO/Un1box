package org.lighttouch.android

/** Process-local UI authority. Pure methods never call Binder or platform code. */
internal class ActiveDisplayOwner {
    private var owner: Any? = null
    private var generation = 0L
    @Synchronized fun activate(value: Any): Long {
        if (owner !== value) { check(generation < Long.MAX_VALUE); generation++; owner = value }
        return generation
    }
    @Synchronized fun owns(value: Any, ticket: Long): Boolean = ticket > 0 && owner === value && ticket == generation
    @Synchronized fun retire(value: Any, ticket: Long): Boolean {
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
    fun bind(identity: Any, value: Long) { require(value > 0); worker = identity; token = value }
    fun forWorker(identity: Any): Long = if (worker === identity) token else 0L
    fun clear() { worker = null; token = 0L }
}

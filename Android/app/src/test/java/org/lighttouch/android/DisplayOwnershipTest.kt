package org.lighttouch.android

import org.junit.Assert.*
import org.junit.Test

class DisplayOwnershipTest {
    @Test fun oldActivityCannotRetireOrReconnectOverNewOwner() {
        val gate = ActiveDisplayOwner(); val a = Any(); val b = Any()
        val old = gate.activate(a); val latest = gate.activate(b)
        assertFalse(gate.owns(a, old)); assertFalse(gate.retire(a, old))
        assertTrue(gate.owns(b, latest))
        // A reconnect/callback only queries this gate; it cannot reactivate A.
        repeat(3) { assertFalse(gate.owns(a, old)); assertTrue(gate.owns(b, latest)) }
    }
    @Test fun duplicateActivationReusesTicketButActualForegroundReturnIsNew() {
        val gate = ActiveDisplayOwner(); val a = Any(); val b = Any()
        val first = gate.activate(a)
        assertEquals(first, gate.activate(a))
        gate.activate(b)
        val resumed = gate.activate(a)
        assertTrue(resumed > first); assertFalse(gate.owns(a, first))
        assertTrue(gate.retire(a, resumed)); assertFalse(gate.owns(a, resumed))
    }
    @Test fun workerIdentityPreventsReusedNumericLeaseFromEscapingOldBinding() {
        val oldWorker = Any(); val newWorker = Any(); val lease = WorkerDisplayLease()
        lease.bind(oldWorker, 1)
        assertEquals(1L, lease.forWorker(oldWorker)); assertEquals(0L, lease.forWorker(newWorker))
        lease.bind(newWorker, 1)
        assertEquals(0L, lease.forWorker(oldWorker)); assertEquals(1L, lease.forWorker(newWorker))
        lease.clear(); assertEquals(0L, lease.forWorker(newWorker))
    }
}

package org.lighttouch.android

import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicBoolean

/** Sink lifetime tests; symbolic writes are not synthetic guest/audio acceptance. */
class AudioPacketEpochTest {
    @Test fun readCapturedBeforePauseCannotWriteAfterResume() {
        val gate = AudioPacketEpoch(); gate.resume()
        val captured = CountDownLatch(1); val readReturns = CountDownLatch(1)
        val written = AtomicInteger(0)
        val reader = Thread {
            val ticket = gate.capture() // Same production boundary before Native.audio.
            captured.countDown(); readReturns.await()
            synchronized(gate) { if (gate.isCurrent(ticket)) written.incrementAndGet() }
        }
        reader.start()
        try {
            assertTrue(captured.await(1, TimeUnit.SECONDS))
            gate.pause(); gate.resume() // Complete loss/gain while the read is in flight.
            readReturns.countDown(); reader.join(1000)
            assertFalse(reader.isAlive); assertEquals(0, written.get())
            val fresh = gate.capture()
            synchronized(gate) { if (gate.isCurrent(fresh)) written.incrementAndGet() }
            assertEquals(1, written.get())
        } finally { readReturns.countDown(); reader.join(1000) }
    }
    @Test fun closedReadCannotBecomeEligibleAfterResumeAndDuplicatePlayKeepsFreshRead() {
        val gate = AudioPacketEpoch()
        val closed = gate.capture(); assertEquals(0L, closed)
        gate.resume(); assertFalse(gate.isCurrent(closed))
        val fresh = gate.capture(); gate.resume()
        assertTrue(gate.isCurrent(fresh)) // Repeated play with held focus is not a transition.
        gate.pause(); val duringPause = gate.capture(); gate.resume()
        assertFalse(gate.isCurrent(fresh)); assertFalse(gate.isCurrent(duringPause))
        assertTrue(gate.isCurrent(gate.capture()))
    }
    @Test fun writingCriticalSectionCompletesBeforePausePublishesItsFence() {
        val gate = AudioPacketEpoch(); gate.resume(); val old = gate.capture()
        val writing = CountDownLatch(1); val releaseWrite = CountDownLatch(1)
        val pausePublished = AtomicBoolean(false); val writes = AtomicInteger(0)
        val writer = Thread {
            synchronized(gate) {
                if (gate.isCurrent(old)) { writing.countDown(); releaseWrite.await(); writes.incrementAndGet() }
            }
        }
        val pauser = Thread { gate.pause(); pausePublished.set(true) }
        writer.start()
        try {
            assertTrue(writing.await(1, TimeUnit.SECONDS)); pauser.start()
            val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(1)
            while (pauser.state != Thread.State.BLOCKED && System.nanoTime() < deadline) Thread.sleep(1)
            assertEquals("Pause did not contend with the actual sink monitor", Thread.State.BLOCKED, pauser.state)
            assertFalse(pausePublished.get())
            releaseWrite.countDown(); writer.join(1000); pauser.join(1000)
            assertFalse(writer.isAlive); assertFalse(pauser.isAlive); assertTrue(pausePublished.get())
            assertEquals(1, writes.get()); gate.resume(); assertFalse(gate.isCurrent(old))
        } finally { releaseWrite.countDown(); writer.join(1000); if (pauser.state != Thread.State.NEW) pauser.join(1000) }
    }
}

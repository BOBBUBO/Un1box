package org.lighttouch.android

import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

/** Tests the production coordinator with blocked functions; never loads/kills a native worker. */
class ShutdownWatchdogTest {
    private fun nowMillis(): Long = System.nanoTime() / 1_000_000
    @Test fun deadlineSurvivesBlockedAdmissionCancellationAndStatus() {
        for (blockedStage in listOf("shutdown-admission", "contact-cancellation", "terminal-poll")) {
            val entered = CountDownLatch(1)
            val release = CountDownLatch(1)
            val returned = CountDownLatch(1)
            val disposed = CountDownLatch(1)
            val disposalCount = AtomicInteger(0)
            val observed = AtomicReference<ShutdownReceipt>()
            fun block(stage: String) {
                if (stage == blockedStage) {
                    entered.countDown()
                    try { assertTrue(release.await(3, TimeUnit.SECONDS)) } finally { returned.countDown() }
                }
            }
            val coordinator = ShutdownWatchdog(150, ::nowMillis,
                requestNativeStop = { block("shutdown-admission"); "" },
                cancelNativeContacts = { block("contact-cancellation") },
                nativeStatus = { block("terminal-poll"); arrayOf("stopped", "", "") },
                dispose = { observed.set(it); disposalCount.incrementAndGet(); disposed.countDown() })
            try {
                val before = nowMillis()
                assertTrue(coordinator.request())
                assertTrue("Request waited on blocked native path", nowMillis() - before < 100)
                assertTrue("Native path never reached $blockedStage", entered.await(1, TimeUnit.SECONDS))
                assertTrue("Independent disposal deadline did not fire", disposed.await(2, TimeUnit.SECONDS))
                val receipt = requireNotNull(observed.get())
                assertEquals("deadline-unconfirmed", receipt.reason)
                assertEquals(blockedStage, receipt.progress.stage)
                assertNull(receipt.terminalState)
                assertTrue(receipt.observedAtMs >= receipt.deadlineMs)
                assertTrue("Fallback was excessively delayed", receipt.observedAtMs - receipt.deadlineMs < 1000)
                assertFalse("Duplicate stop request admitted", coordinator.request())
            } finally { release.countDown() }
            assertTrue("Released native test callback did not return", returned.await(1, TimeUnit.SECONDS))
            Thread.sleep(200)
            assertEquals("Late native completion disposed twice", 1, disposalCount.get())
        }
    }

    @Test fun nativeTerminalWinsOnceBeforeGraceDeadline() {
        val disposed = CountDownLatch(1)
        val count = AtomicInteger(0)
        val receipt = AtomicReference<ShutdownReceipt>()
        val coordinator = ShutdownWatchdog(150, ::nowMillis, { "" }, {}, { arrayOf("stopped", "", "") }, {
            receipt.set(it); count.incrementAndGet(); disposed.countDown()
        })
        assertTrue(coordinator.request()); assertTrue(disposed.await(1, TimeUnit.SECONDS))
        assertEquals("native-terminal", receipt.get().reason)
        assertEquals("stopped", receipt.get().terminalState)
        assertTrue(receipt.get().observedAtMs < receipt.get().deadlineMs)
        Thread.sleep(300)
        assertEquals("Cancelled timer disposed twice", 1, count.get())
    }

    @Test fun blockedReceiptWriterCannotDelayDeadlineTermination() {
        val nativeEntered = CountDownLatch(1)
        val releaseNative = CountDownLatch(1)
        val nativeReturned = CountDownLatch(1)
        val writerEntered = CountDownLatch(1)
        val releaseWriter = CountDownLatch(1)
        val writerReturned = CountDownLatch(1)
        val terminated = CountDownLatch(1)
        val terminationCount = AtomicInteger(0)
        val observed = AtomicReference<ShutdownReceipt>()
        val coordinator = ShutdownWatchdog(150, ::nowMillis,
            requestNativeStop = {
                nativeEntered.countDown()
                try { assertTrue(releaseNative.await(3, TimeUnit.SECONDS)); "" } finally { nativeReturned.countDown() }
            }, cancelNativeContacts = {}, nativeStatus = { arrayOf("stopped", "", "") }, dispose = { receipt ->
                disposeWithoutWaitingForReceipt(writeReceipt = {
                    writerEntered.countDown()
                    try { assertTrue(releaseWriter.await(3, TimeUnit.SECONDS)) } finally { writerReturned.countDown() }
                }, terminate = { observed.set(receipt); terminationCount.incrementAndGet(); terminated.countDown() })
            })
        try {
            assertTrue(coordinator.request()); assertTrue(nativeEntered.await(1, TimeUnit.SECONDS))
            assertTrue("Termination waited for receipt IO", terminated.await(2, TimeUnit.SECONDS))
            assertTrue("Receipt writer was not actually exercised", writerEntered.await(1, TimeUnit.SECONDS))
            assertEquals("Writer released before termination proof", 1L, releaseWriter.count)
            assertEquals("deadline-unconfirmed", observed.get().reason)
            assertTrue(observed.get().observedAtMs >= observed.get().deadlineMs)
            assertEquals(1, terminationCount.get())
        } finally { releaseNative.countDown(); releaseWriter.countDown() }
        assertTrue(nativeReturned.await(1, TimeUnit.SECONDS)); assertTrue(writerReturned.await(1, TimeUnit.SECONDS))
        Thread.sleep(200)
        assertEquals("Late native/writer completion terminated twice", 1, terminationCount.get())
    }
}

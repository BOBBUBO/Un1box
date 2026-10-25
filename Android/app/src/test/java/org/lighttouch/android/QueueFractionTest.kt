package org.lighttouch.android

import org.junit.Assert.*
import org.junit.Test

class QueueFractionTest {
    private fun item(id: Long, state: QueueState, progress: Float? = null) = QueueItem(id, "k$id", "App $id", state, progress = progress)

    @Test fun anEmptyQueueHasNoProgress() = assertEquals(0f, queueFraction(emptyList()), 0f)

    @Test fun anItemIsHalfDoneOnceDownloadedAndDoneOnceInstalled() {
        assertEquals(0.25f, queueFraction(listOf(item(1, QueueState.DOWNLOADING, 0.5f))), 0.001f)
        assertEquals(0.5f, queueFraction(listOf(item(1, QueueState.READY))), 0.001f)
        assertEquals(0.75f, queueFraction(listOf(item(1, QueueState.INSTALLING, 0.5f))), 0.001f)
        assertEquals(1f, queueFraction(listOf(item(1, QueueState.DONE))), 0.001f)
    }

    @Test fun finishedItemsCountWholeAmongWaitingOnes() {
        val items = listOf(item(1, QueueState.DONE), item(2, QueueState.FAILED), item(3, QueueState.PENDING), item(4, QueueState.PENDING))
        assertEquals(0.5f, queueFraction(items), 0.001f)
    }

    @Test fun anUnknownStepCountsAsNotStarted() = assertEquals(0f, queueFraction(listOf(item(1, QueueState.DOWNLOADING, null))), 0f)
}

package org.lighttouch.android

import android.os.Binder
import android.os.SystemClock
import android.view.MotionEvent
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.lang.reflect.Proxy

/** Production View with fake Binder responses, never guest/native acceptance. */
@RunWith(AndroidJUnit4::class)
class GuestDisplayAdmissionTest {
    private class Endpoint {
        val calls = mutableListOf<String>()
        var rejection = ""
        val worker = Proxy.newProxyInstance(IEngine::class.java.classLoader, arrayOf(IEngine::class.java)) { _, method, _ ->
            when (method.name) {
                "touch" -> { calls += "touch"; rejection }
                "cancelInput" -> { calls += "cancel"; null }
                "surface" -> { calls += "surface"; null }
                "asBinder" -> Binder()
                "toString" -> "Fake View admission endpoint"
                else -> error("Unexpected View control: ${method.name}")
            }
        } as IEngine
    }
    private fun send(display: GuestDisplay, action: Int) {
        val now = SystemClock.uptimeMillis()
        val event = MotionEvent.obtain(now, now, action, 160f, 240f, 0)
        try { assertTrue(display.dispatchTouchEvent(event)) } finally { event.recycle() }
    }
    private fun onMain(action: () -> Unit) {
        var failure: Throwable? = null
        InstrumentationRegistry.getInstrumentation().runOnMainSync {
            try { action() } catch (caught: Throwable) { failure = caught }
        }
        // Assertion failures belong to JUnit, not the application's main Looper.
        failure?.let { throw it }
    }
    @Test fun changedViewportCancelsBeforeSurfaceAndSuppressesOldContinuations() = onMain {
        val endpoint = Endpoint()
        val display = GuestDisplay(InstrumentationRegistry.getInstrumentation().targetContext) { endpoint.worker }
        display.layout(0, 0, 320, 480)
        display.surfaceChanged(display.holder, 1, 320, 480)
        assertEquals(listOf("surface"), endpoint.calls)
        send(display, MotionEvent.ACTION_DOWN)
        val owned = requireNotNull(display.inputObservations().getIntArray("androidContactIds"))
        assertArrayEquals(intArrayOf(0), owned)
        val before = endpoint.calls.size
        display.surfaceChanged(display.holder, 1, 320, 480)
        assertEquals(listOf("surface"), endpoint.calls.drop(before))
        assertArrayEquals(owned, display.inputObservations().getIntArray("androidContactIds"))
        val resize = endpoint.calls.size
        display.layout(0, 0, 480, 320)
        display.surfaceChanged(display.holder, 1, 480, 320)
        assertEquals("Contact release must precede new mapping", listOf("cancel", "surface"), endpoint.calls.drop(resize))
        assertArrayEquals(intArrayOf(), display.inputObservations().getIntArray("androidContactIds"))
        assertTrue(display.inputObservations().getBoolean("awaitingNewDown"))
        val afterResize = endpoint.calls.size
        send(display, MotionEvent.ACTION_MOVE); send(display, MotionEvent.ACTION_UP)
        assertEquals("Old contact continuation reached Binder", afterResize, endpoint.calls.size)
        send(display, MotionEvent.ACTION_DOWN); send(display, MotionEvent.ACTION_UP)
        assertEquals(listOf("touch", "touch"), endpoint.calls.drop(afterResize))
        assertArrayEquals(intArrayOf(), display.inputObservations().getIntArray("androidContactIds"))
    }
    @Test fun nonemptyNativeRejectionsDropViewOwnershipUntilFreshDown() = onMain {
        for (message in listOf("Touch left guest viewport; gesture cancelled", "No active guest frame; gesture cancelled", "No active guest session; touch was not admitted")) {
            val endpoint = Endpoint()
            val errors = mutableListOf<String>()
            val display = GuestDisplay(InstrumentationRegistry.getInstrumentation().targetContext, { errors += it }) { endpoint.worker }
            display.layout(0, 0, 320, 480)
            send(display, MotionEvent.ACTION_DOWN)
            endpoint.rejection = message
            send(display, MotionEvent.ACTION_MOVE)
            assertEquals(listOf(message), errors)
            assertTrue(display.inputObservations().getBoolean("awaitingNewDown"))
            assertArrayEquals(intArrayOf(), display.inputObservations().getIntArray("androidContactIds"))
            val rejectedAt = endpoint.calls.size
            endpoint.rejection = ""
            send(display, MotionEvent.ACTION_MOVE); send(display, MotionEvent.ACTION_UP)
            assertEquals(rejectedAt, endpoint.calls.size)
            send(display, MotionEvent.ACTION_DOWN); send(display, MotionEvent.ACTION_UP)
            assertEquals(listOf("touch", "touch"), endpoint.calls.drop(rejectedAt))
        }
    }
}

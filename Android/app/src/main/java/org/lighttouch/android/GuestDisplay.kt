package org.lighttouch.android

import android.content.Context
import android.os.RemoteException
import android.os.Bundle
import android.view.MotionEvent
import android.view.SurfaceHolder
import android.view.SurfaceView

/** Pointer IDs survive crossing and index changes. Pixel mapping belongs to native domain. */
class GuestDisplay(context: Context, private val onError: (String) -> Unit = {}, private val engine: () -> IEngine?) : SurfaceView(context), SurfaceHolder.Callback {
    // A newly created view cannot continue contacts owned by the previous Surface.
    private var rejectedGesture = true
    private var surfaceWidth = 0
    private var surfaceHeight = 0
    private var forwardedBatches = 0L
    private var rejectedBatches = 0L
    private var cancellationRequests = 0L
    private var suppressedContinuations = 0L
    private val contactIds = mutableSetOf<Int>()
    /** Main-thread observations of Android admission, never guest delivery proof. */
    internal fun inputObservations(): Bundle = Bundle().apply {
        putLong("forwardedBatches", forwardedBatches)
        putLong("rejectedBatches", rejectedBatches)
        putLong("cancellationRequests", cancellationRequests)
        putLong("suppressedContinuations", suppressedContinuations)
        putIntArray("androidContactIds", contactIds.toIntArray().sortedArray())
        putBoolean("awaitingNewDown", rejectedGesture)
    }
    init {
        contentDescription = "Guest display"
        holder.addCallback(this)
        isFocusable = true
    }
    private fun control(operation: (IEngine) -> Unit) {
        try { engine()?.let(operation) } catch (failure: RemoteException) {
            rejectedGesture = true
            contactIds.clear()
            onError("Guest worker unavailable; input was cancelled. Start a fresh worker.")
        }
    }
    private fun cancelGesture() {
        rejectedGesture = true; contactIds.clear()
        control { cancellationRequests++; it.cancelInput() }
    }
    /** A user Pause ends the Android gesture as well as the native contact lease. */
    internal fun cancelOwnedGesture() = cancelGesture()
    override fun surfaceCreated(holder: SurfaceHolder) { control { it.surface(holder.surface) } }
    override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {
        // A retained contact cannot cross into a different pixel-to-guest viewport.
        // First attach and duplicate callbacks preserve the current ownership state.
        if (surfaceWidth > 0 && surfaceHeight > 0 && (surfaceWidth != width || surfaceHeight != height)) cancelOwnedGesture()
        surfaceWidth = width; surfaceHeight = height
        control { it.surface(holder.surface) }
    }
    override fun surfaceDestroyed(holder: SurfaceHolder) { cancelGesture(); control { it.clearSurface() } }
    override fun onWindowFocusChanged(hasWindowFocus: Boolean) {
        super.onWindowFocusChanged(hasWindowFocus)
        if (!hasWindowFocus) cancelGesture()
    }
    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (event.actionMasked == MotionEvent.ACTION_CANCEL) { cancelGesture(); return true }
        if (event.actionMasked == MotionEvent.ACTION_DOWN) rejectedGesture = false
        if (rejectedGesture) { suppressedContinuations++; return true }
        if (event.pointerCount > 8) { cancelGesture(); return true }
        if (event.actionMasked !in listOf(MotionEvent.ACTION_DOWN, MotionEvent.ACTION_POINTER_DOWN, MotionEvent.ACTION_MOVE,
                MotionEvent.ACTION_UP, MotionEvent.ACTION_POINTER_UP)) return true
        val ids = IntArray(event.pointerCount)
        val phases = IntArray(event.pointerCount) { 1 }
        val xy = FloatArray(event.pointerCount * 2)
        for (index in 0 until event.pointerCount) {
            ids[index] = event.getPointerId(index)
            xy[index * 2] = event.getX(index)
            xy[index * 2 + 1] = event.getY(index)
        }
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN, MotionEvent.ACTION_POINTER_DOWN -> phases[event.actionIndex] = 0
            MotionEvent.ACTION_UP, MotionEvent.ACTION_POINTER_UP -> phases[event.actionIndex] = 2
        }
        control { worker ->
            // Preserve the source time; the dedicated worker orders millisecond ties
            // across Views without hiding a genuinely backwards input timestamp.
            val admission = worker.touch(event.eventTime * 1_000_000, ids, phases, xy, width, height, display?.rotation?.times(90) ?: 0)
            forwardedBatches++
            if (admission.isNotEmpty()) { rejectedBatches++; rejectedGesture = true; contactIds.clear(); onError(admission) }
            else {
                for (index in ids.indices) {
                    if (phases[index] == 2) contactIds.remove(ids[index]) else contactIds.add(ids[index])
                }
            }
        }
        if (event.actionMasked == MotionEvent.ACTION_UP) { rejectedGesture = true; performClick() }
        return true
    }
    override fun performClick(): Boolean { super.performClick(); return true }
}

package org.lighttouch.android

import android.content.Context
import android.os.RemoteException
import android.view.MotionEvent
import android.view.SurfaceHolder
import android.view.SurfaceView

/** Pointer IDs survive crossing and index changes. Pixel mapping belongs to native domain. */
class GuestDisplay(context: Context, private val onError: (String) -> Unit = {}, private val engine: () -> IEngine?) : SurfaceView(context), SurfaceHolder.Callback {
    private var rejectedGesture = false
    init {
        contentDescription = "Guest display"
        holder.addCallback(this)
        isFocusable = true
    }
    private fun control(operation: (IEngine) -> Unit) {
        try { engine()?.let(operation) } catch (failure: RemoteException) {
            rejectedGesture = true
            onError("Guest worker unavailable; input was cancelled. Start a fresh worker.")
        }
    }
    private fun cancelGesture() { rejectedGesture = true; control { it.cancelInput() } }
    override fun surfaceCreated(holder: SurfaceHolder) { control { it.surface(holder.surface) } }
    override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) { control { it.surface(holder.surface) } }
    override fun surfaceDestroyed(holder: SurfaceHolder) { cancelGesture(); control { it.clearSurface() } }
    override fun onWindowFocusChanged(hasWindowFocus: Boolean) {
        super.onWindowFocusChanged(hasWindowFocus)
        if (!hasWindowFocus) cancelGesture()
    }
    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (event.actionMasked == MotionEvent.ACTION_CANCEL) { cancelGesture(); return true }
        if (event.actionMasked == MotionEvent.ACTION_DOWN) rejectedGesture = false
        if (rejectedGesture) return true
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
            val admission = worker.touch(event.eventTime * 1_000_000, ids, phases, xy, width, height, display.rotation * 90)
            if (admission.isNotEmpty()) { rejectedGesture = true; onError(admission) }
        }
        if (event.actionMasked == MotionEvent.ACTION_UP) performClick()
        return true
    }
    override fun performClick(): Boolean { super.performClick(); return true }
}

package org.un1box.android

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.ImageFormat
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CaptureRequest
import android.hardware.camera2.params.StreamConfigurationMap
import android.media.Image
import android.media.ImageReader
import android.os.Handler
import android.os.HandlerThread
import android.util.Size

/**
 * This phone's back camera into the emulated device's ISP.
 *
 * One camera, and only the back one: the guest asks its ISP for a frame whatever its camera app is showing, and
 * the engine serves the newest picture it has. There is deliberately nothing here to choose between sensors --
 * a capture that waits for the guest to name a sensor is a capture that can wait for ever, and a viewfinder that
 * opens the wrong lens shows nothing at all.
 *
 * The path is Camera2 -> ImageReader(YUV_420_888) -> Native.cameraFrame, which turns Android's three planes into
 * one NV12 frame (camera_planes.h, tested on its own) and hands it to the engine, whose ISP scales it into the
 * buffer the guest is capturing into. 640x480 is the size every phone gives and what iOS previews at.
 *
 * Nothing here waits for the guest. A frame the guest is not drawing yet is replaced by the next one in the
 * engine, so a guest that is slow costs a dropped frame, never a queue and never heat.
 *
 * Everything runs on one camera thread; start and stop are called from the worker's thread and never block on
 * the camera.
 */
internal class CameraCapture(private val context: Context, private val onError: (String) -> Unit) {
    @Volatile private var wanted = false
    @Volatile private var device: CameraDevice? = null
    private var session: CameraCaptureSession? = null
    private var reader: ImageReader? = null
    private var thread: HandlerThread? = null
    private var handler: Handler? = null
    /** When the last frame went to the engine: the phone captures faster than the device draws. */
    private var lastFrameNs = 0L
    /** Frames handed to the engine since the capture opened, for the Settings line. */
    @Volatile private var delivered = 0L

    fun start() {
        if (wanted) return
        wanted = true
        delivered = 0
        val worker = HandlerThread("camera").apply { start() }
        thread = worker
        handler = Handler(worker.looper).also { it.post { open() } }
    }

    fun stop() {
        wanted = false
        handler?.post { close() }
        thread?.quitSafely()
        thread = null
        handler = null
        Native.cameraFlush()
    }

    /** The camera id for a lens, or null on a phone without that camera. */
    private fun cameraId(manager: CameraManager, facing: Int): String? = manager.cameraIdList.firstOrNull { id ->
        manager.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING) == facing
    }

    /** Closest to 640x480 first, 4:3 before widescreen so the picture is not letterboxed. */
    private fun sizes(map: StreamConfigurationMap?): List<Size> {
        val all = map?.getOutputSizes(ImageFormat.YUV_420_888) ?: emptyArray()
        val usable = all.filter { it.width * it.height <= 1280 * 960 && it.width % 2 == 0 && it.height % 2 == 0 }
            .sortedBy { kotlin.math.abs(it.width.toLong() * it.height - 640L * 480L) }
        return usable.ifEmpty { listOf(Size(640, 480)) }.take(3)
    }

    @SuppressLint("MissingPermission")   // the settings switch asks for it before this ever runs
    private fun open() {
        val manager = context.getSystemService(CameraManager::class.java) ?: run {
            onError("Camera unavailable: Android has no camera service.")
            return
        }
        val id = cameraId(manager, CameraCharacteristics.LENS_FACING_BACK) ?: run {
            onError("Camera unavailable: this phone reports no back camera.")
            return
        }
        val map = manager.getCameraCharacteristics(id).get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
        var target: ImageReader? = null
        for (size in sizes(map)) {
            target = runCatching { ImageReader.newInstance(size.width, size.height, ImageFormat.YUV_420_888, 2) }
                .getOrNull()
            if (target != null) break
        }
        val created = target ?: run {
            onError("Camera unavailable: this phone offers no YUV size the device can use.")
            return
        }
        reader = created
        created.setOnImageAvailableListener({ source ->
            val image = try { source.acquireLatestImage() } catch (_: IllegalStateException) { null }
                ?: return@setOnImageAvailableListener
            try { deliver(image) } finally { image.close() }
        }, handler)
        try {
            manager.openCamera(id, object : CameraDevice.StateCallback() {
                override fun onOpened(camera: CameraDevice) {
                    if (!wanted) { camera.close(); return }
                    device = camera
                    onError("")
                    startSession(camera, created)
                }
                override fun onDisconnected(camera: CameraDevice) {
                    camera.close()
                    if (device === camera) device = null
                    reopen("the camera was disconnected")
                }
                override fun onError(camera: CameraDevice, error: Int) {
                    camera.close()
                    if (device === camera) device = null
                    reopen("the camera reported error $error")
                }
            }, handler)
        } catch (failure: Exception) {
            reopen(failure.message ?: "the camera could not be opened")
        }
    }

    /**
     * Try again shortly. Android refuses a camera that is still being released (CAMERA_IN_USE) and a phone's
     * camera service can be busy for a moment, so one refusal must not end the capture: the guest would wait for
     * frames that never come.
     */
    private fun reopen(reason: String) {
        if (!wanted) return
        onError("Camera: $reason; trying again")
        handler?.postDelayed({
            if (!wanted) return@postDelayed
            close()
            open()
        }, REOPEN_GAP_MS)
    }

    private fun startSession(camera: CameraDevice, target: ImageReader) {
        try {
            val request = camera.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW).apply {
                addTarget(target.surface)
                // The viewfinder's own controls: continuous autofocus, auto exposure and auto white balance,
                // which is what iOS asks its sensor for and what makes a preview look like a camera.
                set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
                set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON)
                set(CaptureRequest.CONTROL_AWB_MODE, CaptureRequest.CONTROL_AWB_MODE_AUTO)
            }.build()
            camera.createCaptureSession(listOf(target.surface), object : CameraCaptureSession.StateCallback() {
                override fun onConfigured(ready: CameraCaptureSession) {
                    if (!wanted) { ready.close(); return }
                    session = ready
                    try {
                        ready.setRepeatingRequest(request, null, handler)
                    } catch (failure: Exception) {
                        reopen("the preview request failed (${failure.message ?: "unknown"})")
                    }
                }

                override fun onConfigureFailed(ready: CameraCaptureSession) {
                    reopen("this phone refused the capture session")
                }
            }, handler)
        } catch (failure: Exception) {
            reopen("the capture session could not be made (${failure.message ?: "unknown"})")
        }
    }

    /** One frame: its three planes and their strides go to the engine's conversion. */
    private fun deliver(image: Image) {
        val planes = image.planes
        if (planes.size < 3) return
        // The phone offers thirty frames a second and the emulated ISP draws fifteen. Converting the ones in
        // between is a pass over half a megabyte each, on the app's own thread, for pictures nothing draws.
        val now = System.nanoTime()
        if (now - lastFrameNs < FRAME_GAP_NS) return
        lastFrameNs = now
        val taken = try {
            Native.cameraFrame(
                planes[0].buffer, planes[0].rowStride, planes[0].pixelStride,
                planes[1].buffer, planes[1].rowStride, planes[1].pixelStride,
                planes[2].buffer, planes[2].rowStride, planes[2].pixelStride,
                image.width, image.height, Native.CAMERA_BACK,
            )
        } catch (failure: Exception) {
            onError("Camera frame failed: ${failure.message ?: "unknown error"}")
            return
        }
        // Positive: the engine has it. Zero: the guest is not capturing yet, which is not an error. Negative:
        // the engine refused the frame, and only a frame that is not a frame does that.
        if (taken > 0) delivered++ else if (taken < 0) onError("The device refused the camera frame (${-taken}).")
    }

    /** How many frames this capture has handed to the engine: the Settings line's own view of it. */
    val framesDelivered: Long get() = delivered

    private fun close() {
        runCatching { session?.close() }
        runCatching { device?.close() }
        runCatching { reader?.close() }
        session = null
        device = null
        reader = null
    }

    companion object {
        /** How long to let the camera service release a camera before opening one again. */
        private const val REOPEN_GAP_MS = 300L
        /** ~16 frames a second, which is what the emulated ISP draws: the rest would only be converted. */
        private const val FRAME_GAP_NS = 60_000_000L
    }
}

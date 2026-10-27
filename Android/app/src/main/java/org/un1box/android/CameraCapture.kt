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
import java.util.concurrent.atomic.AtomicBoolean

/**
 * The phone's cameras into the emulated device: the back one and the front one.
 *
 * One camera is open at a time, because both the phone and the emulated device use one sensor at a time. Which
 * one is decided by the guest: its driver names a sensor when it starts the camera and again when the Camera
 * app's own switch flips, the engine reports that (Native.cameraSensor), and this follows it.
 *
 * The path is Camera2 -> ImageReader(YUV_420_888) -> Native.cameraFrame, which turns Android's three planes into
 * one NV12 frame (camera_planes.h, tested on its own) and hands it to the engine, whose ISP draws it into the
 * buffer the guest is capturing into. 640x480 is the size every phone gives and what iOS previews at; the engine
 * scales it to whatever mode the guest asked for.
 *
 * Nothing here waits for the guest. A frame the guest is not drawing yet is replaced by the next one in the
 * engine, so a guest that is slow costs a dropped frame, never a queue and never heat.
 *
 * Everything runs on one camera thread; start, follow and stop are called from the worker's thread and never
 * block on the camera.
 */
internal class CameraCapture(private val context: Context, private val onError: (String) -> Unit) {
    /** Which camera this phone's lens is on: LENS_FACING_BACK or LENS_FACING_FRONT. */
    @Volatile private var lens = CameraCharacteristics.LENS_FACING_BACK
    @Volatile private var wanted = false
    /** The guest named another sensor and this is opening it: the poll must not interfere. */
    private val opening = AtomicBoolean(false)
    @Volatile private var device: CameraDevice? = null
    private var session: CameraCaptureSession? = null
    private var reader: ImageReader? = null
    private var thread: HandlerThread? = null
    private var handler: Handler? = null
    /** When the last frame went to the engine: the phone captures faster than the device draws. */
    private var lastFrameNs = 0L

    fun start(sensor: Int) {
        if (wanted) return
        wanted = true
        lens = lensFor(sensor)
        val worker = HandlerThread("camera").apply { start() }
        thread = worker
        handler = Handler(worker.looper).also { it.post { open() } }
    }

    /**
     * The guest is on another sensor now. The camera service releases the old device asynchronously, so the new
     * one is opened after a short gap; opening both in the same breath is refused with CAMERA_IN_USE on some
     * phones.
     */
    fun follow(sensor: Int) {
        if (!wanted) return
        val target = lensFor(sensor)
        if (target == lens) return
        lens = target
        handler?.post {
            if (!wanted) return@post
            close()
            handler?.postDelayed({ if (wanted) open() }, REOPEN_GAP_MS)
        }
    }

    fun stop() {
        wanted = false
        handler?.post { close() }
        thread?.quitSafely()
        thread = null
        handler = null
        Native.cameraFlush()
    }

    private fun lensFor(sensor: Int) =
        if (sensor == Native.CAMERA_FRONT) CameraCharacteristics.LENS_FACING_FRONT else CameraCharacteristics.LENS_FACING_BACK

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
        val manager = context.getSystemService(CameraManager::class.java) ?: return
        val id = cameraId(manager, lens) ?: run {
            onError(if (lens == CameraCharacteristics.LENS_FACING_FRONT)
                "This phone has no front camera; the device keeps the back one."
            else "This phone has no back camera; the device keeps the front one.")
            return
        }
        val characteristics = manager.getCameraCharacteristics(id)
        val map = characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
        var target: ImageReader? = null
        for (size in sizes(map)) {
            target = runCatching { ImageReader.newInstance(size.width, size.height, ImageFormat.YUV_420_888, 2) }
                .getOrNull()
            if (target != null) break
        }
        val created = target ?: run {
            onError("The camera lists no size this device can use.")
            return
        }
        reader = created
        opening.set(true)
        created.setOnImageAvailableListener({ source ->
            val image = try { source.acquireLatestImage() } catch (_: IllegalStateException) { null }
                ?: return@setOnImageAvailableListener
            try { deliver(image) } finally { image.close() }
        }, handler)
        try {
            manager.openCamera(id, object : CameraDevice.StateCallback() {
                override fun onOpened(camera: CameraDevice) {
                    opening.set(false)
                    if (!wanted) { camera.close(); return }
                    device = camera
                    onError("")
                    startSession(camera, created)
                }
                override fun onDisconnected(camera: CameraDevice) {
                    opening.set(false)
                    camera.close()
                    if (device === camera) device = null
                    reopen("the camera was disconnected")
                }
                override fun onError(camera: CameraDevice, error: Int) {
                    opening.set(false)
                    camera.close()
                    if (device === camera) device = null
                    reopen("the camera reported error $error")
                }
            }, handler)
        } catch (failure: Exception) {
            opening.set(false)
            reopen(failure.message ?: "the camera could not be opened")
        }
    }

    /**
     * Try again shortly. Android refuses a camera that is still being released (CAMERA_IN_USE) and a phone's
     * camera service can be busy for a moment after a switch, so one refusal must not end the capture: the guest
     * would wait for frames that never come.
     */
    private fun reopen(reason: String) {
        opening.set(false)
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
                        onError("Camera: the preview request failed (${failure.message ?: "unknown"})")
                    }
                }

                override fun onConfigureFailed(ready: CameraCaptureSession) {
                    reopen("this phone refused the capture session")
                }
            }, handler)
        } catch (failure: Exception) {
            onError("Camera: the capture session could not be made (${failure.message ?: "unknown"})")
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
        val sensor = if (lens == CameraCharacteristics.LENS_FACING_FRONT) Native.CAMERA_FRONT else Native.CAMERA_BACK
        val taken = try {
            Native.cameraFrame(
                planes[0].buffer, planes[0].rowStride, planes[0].pixelStride,
                planes[1].buffer, planes[1].rowStride, planes[1].pixelStride,
                planes[2].buffer, planes[2].rowStride, planes[2].pixelStride,
                image.width, image.height, sensor,
            )
        } catch (failure: Exception) {
            onError("Camera frame failed: ${failure.message ?: "unknown error"}")
            return
        }
        // Negative: the engine refused it. Zero: the guest is not capturing, which is not an error.
        if (taken < 0) onError("The device refused the camera frame (${-taken}).")
    }

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

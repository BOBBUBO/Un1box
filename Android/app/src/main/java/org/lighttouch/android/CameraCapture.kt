package org.lighttouch.android

import android.content.Context
import android.graphics.ImageFormat
import android.hardware.camera2.CameraAccessException
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
 * This phone's cameras into the emulated device's ISP: the back one and the front one.
 *
 * One camera is open at a time, because that is what a phone does and because the emulated device uses one
 * sensor at a time too: the guest's camera switch is a sensor recognition in its driver, the engine reports
 * which sensor that was (Native.cameraSensor), and the session here follows it. The frame path is
 * Camera2 -> ImageReader(YUV_420_888) -> Native.cameraFrame, which converts the three planes to NV12 with the
 * device's own row and pixel strides (the conversion and its checks live in the C++ side, where they are
 * tested) and hands it to the engine, whose ISP writes it into the buffer the guest is capturing into.
 *
 * Sizes: 640x480, the size every phone's camera supports and what iOS previews at; the ISP model scales it to
 * whatever size the guest asks for, and a still is one of these frames. Nothing here waits for the guest: a
 * frame the guest is not reading is replaced by the next one in the engine, so a slow guest costs nothing.
 *
 * Everything runs on its own camera thread; start/stop/switch are called from the worker's thread and never
 * block on the camera.
 */
internal class CameraCapture(private val context: Context, private val onError: (String) -> Unit) {
    /** Which sensor the guest asked for last: 0 back, 1 front. */
    @Volatile private var facing = CameraCharacteristics.LENS_FACING_BACK
    @Volatile private var wanted = false
    @Volatile private var running = false
    private var thread: HandlerThread? = null
    private var handler: Handler? = null
    private var device: CameraDevice? = null
    private var session: CameraCaptureSession? = null
    private var reader: ImageReader? = null
    private val switching = AtomicBoolean(false)

    /** The camera id for a lens facing, or null on a phone without that camera (an iPod touch has one). */
    private fun cameraId(lens: Int): String? = try {
        val manager = context.getSystemService(CameraManager::class.java)
        manager.cameraIdList.firstOrNull { id ->
            manager.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING) == lens
        }
    } catch (failure: CameraAccessException) {
        onError("Camera unavailable: ${failure.message ?: "the camera service refused"}")
        null
    }

    /** Start with the sensor the guest is using (or the back one before it says). */
    fun start(sensor: Int) {
        if (running) return
        wanted = true
        facing = if (sensor == Native.CAMERA_FRONT) CameraCharacteristics.LENS_FACING_FRONT
                 else CameraCharacteristics.LENS_FACING_BACK
        running = true
        val worker = HandlerThread("camera").apply { start() }
        thread = worker
        val looper = Handler(worker.looper)
        handler = looper
        looper.post { open() }
    }

    /** The guest switched cameras (or the app was told to): reopen on the other one, if this phone has it. */
    fun follow(sensor: Int) {
        if (!wanted) return
        val lens = if (sensor == Native.CAMERA_FRONT) CameraCharacteristics.LENS_FACING_FRONT
                   else CameraCharacteristics.LENS_FACING_BACK
        if (lens == facing || !switching.compareAndSet(false, true)) return
        handler?.post {
            switching.set(false)
            if (!wanted) return@post
            facing = lens
            closeQuietly()
            open()
        }
    }

    fun stop() {
        wanted = false
        handler?.post { closeQuietly() }
        thread?.quitSafely()
        thread = null
        handler = null
        running = false
        Native.cameraFlush()
    }

    private fun open() {
        val id = cameraId(facing) ?: run {
            // This phone has no camera on that side (a front-facing one, usually): say so once and keep the
            // other one, which is what the guest sees anyway.
            onError(if (facing == CameraCharacteristics.LENS_FACING_FRONT)
                "This phone has no front camera; the device keeps the back one."
            else "This phone has no back camera; the device keeps the front one.")
            return
        }
        val manager = context.getSystemService(CameraManager::class.java)
        val map = manager.getCameraCharacteristics(id).get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
            as? StreamConfigurationMap
        val size = chooseSize(map)
        val target = ImageReader.newInstance(size.width, size.height, ImageFormat.YUV_420_888, 2)
        reader = target
        target.setOnImageAvailableListener({ source ->
            val image = try { source.acquireLatestImage() } catch (_: IllegalStateException) { null } ?: return@setOnImageAvailableListener
            try {
                deliver(image)
            } finally {
                image.close()
            }
        }, handler)
        try {
            manager.openCamera(id, object : CameraDevice.StateCallback() {
                override fun onOpened(camera: CameraDevice) {
                    if (!wanted) { camera.close(); return }
                    device = camera
                    startSession(camera, target)
                }
                override fun onDisconnected(camera: CameraDevice) {
                    camera.close(); if (device === camera) device = null
                }
                override fun onError(camera: CameraDevice, error: Int) {
                    camera.close(); if (device === camera) device = null
                    onError("Camera stopped (error $error); the device shows its test pattern.")
                }
            }, handler)
        } catch (failure: Exception) {
            onError("Camera unavailable: ${failure.message ?: "the camera could not be opened"}")
        }
    }

    /** The closest to 640x480 the camera lists, preferring 4:3 so the picture is not letterboxed. */
    private fun chooseSize(map: StreamConfigurationMap?): Size {
        val sizes = map?.getOutputSizes(ImageFormat.YUV_420_888) ?: emptyArray()
        val usable = sizes.filter { it.width * it.height <= 1280 * 960 && it.width % 2 == 0 && it.height % 2 == 0 }
        if (usable.isEmpty()) return Size(640, 480)
        return usable.minByOrNull { kotlin.math.abs(it.width.toLong() * it.height - 640L * 480L) } ?: Size(640, 480)
    }

    private fun startSession(camera: CameraDevice, target: ImageReader) {
        try {
            val request = camera.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW).apply {
                addTarget(target.surface)
                // The device's own viewfinder: continuous autofocus and auto exposure, as iOS asks its sensor for.
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
                        onError("Camera stopped: ${failure.message ?: "the preview request failed"}")
                    }
                }
                override fun onConfigureFailed(ready: CameraCaptureSession) {
                    onError("Camera stopped: this phone refused the capture session.")
                }
            }, handler)
        } catch (failure: Exception) {
            onError("Camera unavailable: ${failure.message ?: "the capture session could not be created"}")
        }
    }

    /** One frame: the three planes, their strides and the size go to the engine's conversion. */
    private fun deliver(image: Image) {
        val planes = image.planes
        if (planes.size < 3) return
        val sensor = if (facing == CameraCharacteristics.LENS_FACING_FRONT) Native.CAMERA_FRONT else Native.CAMERA_BACK
        try {
            val taken = Native.cameraFrame(
                planes[0].buffer, planes[0].rowStride, planes[0].pixelStride,
                planes[1].buffer, planes[1].rowStride, planes[1].pixelStride,
                planes[2].buffer, planes[2].rowStride, planes[2].pixelStride,
                image.width, image.height, sensor,
            )
            // Negative: the engine refused the frame outright (a mismatched size, a sensor it does not have).
            // Zero is a dropped frame -- the guest is not running or is not capturing -- which is not an error.
            if (taken < 0) onError("The engine could not take the camera frame (${-taken}).")
        } catch (failure: Exception) {
            onError("Camera frame failed: ${failure.message ?: "unknown error"}")
        }
    }

    private fun closeQuietly() {
        runCatching { session?.close() }
        runCatching { device?.close() }
        runCatching { reader?.close() }
        session = null
        device = null
        reader = null
    }

    companion object {
        fun choice(key: String?): Choice = Choice.entries.firstOrNull { it.key == key } ?: Choice.FOLLOW
    }

    /**
     * The Settings choice: which camera this phone streams. A nested class of the capture, not a member
     * of its companion object -- `CameraCapture.Choice` does not resolve through a companion object, and
     * `CameraCapture.Companion.Choice` reads badly at every call site.
     */
    enum class Choice(val key: String, val label: String) {
        FOLLOW("follow", "Follow the device"),
        BACK("back", "Back"),
        FRONT("front", "Front"),
    }
}

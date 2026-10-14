package org.lighttouch.android

import android.view.Surface

/** Loaded only in :engine. UI never loads QEMU or constructs boot arguments. */
internal object Native {
    init { System.loadLibrary("lighttouch_jni") }
    external fun importBase(source: String, destination: String): String
    external fun start(device: String): String
    external fun status(): Array<String>
    external fun surface(surface: Surface?)
    external fun pause(paused: Boolean): String
    external fun stop(): String
    external fun button(button: Int, down: Boolean): String
    external fun touch(time: Long, ids: IntArray, phases: IntArray, xy: FloatArray, width: Int, height: Int, rotation: Int): String
    external fun cancelInput()
    external fun motion(kind: Int, time: Long, xyz: FloatArray, rotation: Int): String
    external fun capture(): android.graphics.Bitmap?
    external fun audioInfo(): IntArray
    external fun audio(buffer: ShortArray): Int
}

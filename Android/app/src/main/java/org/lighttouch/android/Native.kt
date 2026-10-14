package org.lighttouch.android

import android.view.Surface

/** Loaded only in :engine. UI never loads QEMU or constructs boot arguments. */
internal object Native {
    init { System.loadLibrary("lighttouch_jni") }
    external fun importBase(source: String, libraryRoot: String, displayName: String, rom: String, guest: String): String
    external fun records(libraryRoot: String): Array<Array<String>>
    external fun select(libraryRoot: String, id: String): String
    external fun start(libraryRoot: String, id: String): String
    external fun recoverStart(libraryRoot: String, id: String): String
    external fun status(): Array<String>
    external fun claimDisplay(): Long
    external fun ownsDisplay(lease: Long): Boolean
    external fun attachDisplay(lease: Long, surface: Surface): String
    external fun releaseDisplay(lease: Long)
    external fun cancelDisplayInput(lease: Long)
    external fun backgroundDisplay(lease: Long): String
    external fun touchDisplay(lease: Long, time: Long, ids: IntArray, phases: IntArray, xy: FloatArray, width: Int, height: Int, rotation: Int): String
    external fun pause(paused: Boolean): String
    external fun stop(): String
    external fun shutdownGraceMillis(): Int
    external fun debugArmQmp(): String
    external fun debugEnableTouchTrace(): String
    external fun debugSelectGraphicsProvider(choice: Int): String
    external fun debugQmpSnapshot(): Array<String>
    external fun button(button: Int, down: Boolean): String
    external fun touch(time: Long, ids: IntArray, phases: IntArray, xy: FloatArray, width: Int, height: Int, rotation: Int): String
    external fun cancelInput()
    external fun motion(kind: Int, time: Long, xyz: FloatArray, rotation: Int): String
    external fun capture(): android.graphics.Bitmap?
    external fun observations(): LongArray
    external fun audioInfo(): IntArray
    external fun guestRequest(operation: Int, argument: String, timeoutMs: Int): Array<String>
    external fun guestPoll(requestId: Long): Array<String>
    external fun guestCancel(requestId: Long)
    external fun audio(buffer: ShortArray): Int
}

package org.lighttouch.android;
import android.os.Bundle;
import android.view.Surface;
interface IEngine {
    Bundle start(String deviceDirectory);
    String importBase(String source, String destination);
    Bundle status();
    void surface(in Surface surface);
    void clearSurface();
    void pause(boolean paused);
    void stop();
    void button(int button, boolean down);
    void touch(long timestampNanos, in int[] ids, in int[] phases, in float[] coordinates, int width, int height, int rotation);
    void cancelInput();
    void motion(int kind, long timestampNanos, in float[] xyz, int rotation);
    String screenshot(String path);
}

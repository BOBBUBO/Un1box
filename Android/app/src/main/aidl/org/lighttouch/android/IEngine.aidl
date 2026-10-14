package org.lighttouch.android;
import android.os.Bundle;
import android.view.Surface;
interface IEngine {
    Bundle start(String recordId);
    String debugArmQmp();
    String debugSelectGraphicsProvider(int choice);
    Bundle debugQmpSnapshot();
    Bundle[] records();
    String select(String recordId);
    String importBase(String source, String displayName);
    String importPrepared(String source, String displayName);
    Bundle status();
    void surface(in Surface surface);
    void clearSurface();
    void pause(boolean paused);
    void stop();
    void button(int button, boolean down);
    String touch(long timestampNanos, in int[] ids, in int[] phases, in float[] coordinates, int width, int height, int rotation);
    void cancelInput();
    void motion(int kind, long timestampNanos, in float[] xyz, int rotation);
    String screenshot(String path);
    Bundle guestRequest(int operation, String argument, int timeoutMs);
    Bundle guestPoll(long requestId);
    void guestCancel(long requestId);
    String debugEnableTouchTrace();
}

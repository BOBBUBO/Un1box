package org.un1box.android;
import android.os.Bundle;
interface ITcgProbe {
    Bundle start();
    Bundle status();
    void finish();
}

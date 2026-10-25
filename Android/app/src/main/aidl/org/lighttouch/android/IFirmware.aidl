package org.lighttouch.android;
import android.os.Bundle;
interface IFirmware {
    // jailbreak: prepare it jailbroken (afc2, Cydia and Substrate), its bootstrap already in CydiaBootstrap.directory
    Bundle start(String firmwareId, String ipswPath, String trustedToolsRoot, String bootromPath, boolean jailbreak);
    Bundle status();
    void cancel();
    void finish();
    boolean supports(String firmwareId);
}

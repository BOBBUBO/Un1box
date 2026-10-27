"""The camera's layers, checked against the real sources and the patch that ships the engine side.

The slices prove what a piece does; these prove the pieces are connected to each other and to the guest:

  engine patch   the patch still applies to a clean engine and commits to it, and still contains the hand-off
                 and the one place the picture's source is chosen
  device model   the ISP takes a frame before it draws the pattern, never draws a size that is not a mode, and
                 turns the front sensor's picture
  native         the JNI entry points the app declares exist, with the names the app asks for
  android        the permission, the switch, the settings line and the worker's own wiring
"""
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATCHES = ROOT / "ci" / "engine-patches"
CAMERA_PATCH = PATCHES / "0010-engine-camera-input.patch"


def read(relative):
    path = ROOT / relative
    if not path.is_file():
        raise AssertionError(f"{relative} is missing")
    return path.read_text(errors="ignore")


def added_lines(patch):
    return "\n".join(line[1:] for line in read(patch).splitlines() if line.startswith("+"))


def file_body(patch, relative):
    """A patch's view of one file: its context and added lines, in order."""
    lines, inside = [], False
    for line in read(patch).splitlines():
        if line.startswith("diff --git "):
            inside = line.endswith(" b/" + relative)
            continue
        if inside and line[:1] in (" ", "+") and not line.startswith("+++"):
            lines.append(line[1:])
    return "\n".join(lines)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


# --- the engine side ---------------------------------------------------------

def engine_patch_ships_the_hand_off():
    body = added_lines(CAMERA_PATCH)
    require("bool host_camera_push(" in body, "the patch no longer adds the camera's frame slots")
    require("const uint8_t *host_camera_take(" in body, "the slots no longer hand a frame to the ISP")
    require("'host-camera.c'" in body, "hw/arm/meson.build does not build host-camera.c")
    for entry in ("qemu_ios_camera_frame", "qemu_ios_camera_flush", "qemu_ios_camera_sensor", "qemu_ios_camera_state"):
        require(entry in body, f"the engine API no longer exports {entry}")


def engine_patch_chooses_the_source_at_recognition():
    """The kext names a sensor exactly there, so that is where the picture's source has to be chosen."""
    isp = file_body(CAMERA_PATCH, "hw/arm/s5l8930_isp.c")
    require("host_camera_select(" in isp, "the ISP never tells the slots which sensor the guest is driving")
    recognition = isp.split("(cat == 0x03 || cat == 0x04) && reg == 0x00", 1)
    require(len(recognition) == 2, "sensor recognition no longer names the sensor")
    require("host_camera_select(" in recognition[1][:600],
            "the sensor is not chosen where the guest names it, so the wrong camera's frames would be drawn")


def engine_patch_draws_the_camera_before_the_pattern():
    """Order matters: a pattern in front of a real source is a picture nobody ever sees."""
    isp = file_body(CAMERA_PATCH, "hw/arm/s5l8930_isp.c")
    require("host_camera_take(" in isp, "the ISP no longer asks the phone's camera for a frame")
    camera = isp.find("isp_from_camera(")
    pattern = isp.find("isp_pattern(s, w, h, nv12)", isp.find("static void isp_frame"))
    require(camera != -1 and pattern != -1 and camera < pattern,
            "the test pattern is drawn before the camera's frame: the phone's picture would never show")


def engine_patch_turns_the_front_sensor_upright():
    isp = file_body(CAMERA_PATCH, "hw/arm/s5l8930_isp.c")
    require("bool flip)" in isp or "bool flip)" in isp.replace("\n", " "),
            "the frame no longer has a way to be turned, so the front camera's picture arrives upside down")
    require("flip ? h - 1 - y : y" in isp, "the turn is not applied to the rows it has to be applied to")


def engine_patch_refuses_a_size_that_is_not_a_mode():
    isp = file_body(CAMERA_PATCH, "hw/arm/s5l8930_isp.c")
    require("ISP_FRAME_MAX" in isp and "is not a mode" in isp,
            "a frame size the sensors never had is no longer skipped: it would be allocated and DMAed")


# --- native and android ------------------------------------------------------

def jni_matches_the_app():
    native = read("Android/app/src/main/java/org/un1box/android/Native.kt")
    jni = read("Android/app/src/main/cpp/un1box_jni.cpp")
    for method in ("cameraFrame", "cameraSensor", "cameraFlush", "cameraInfo"):
        require(f"external fun {method}(" in native, f"the app does not declare {method}")
        require(f"Java_org_un1box_android_Native_{method}(" in jni, f"the JNI side has no {method}")
    require('#include "camera_planes.h"' in jni, "the JNI side does not use the plane conversion")
    require("lt_runtime_camera_write(" in jni, "the JNI side never reaches the portable camera API")


def portable_declares_the_camera():
    header = read("Portable/include/lighttouch.h")
    source = read("Portable/src/Runtime.cpp")
    require("LT_CAMERA_BACK" in header and "LT_CAMERA_FRONT" in header, "the sensors are not named in the API")
    for function in ("lt_runtime_camera_write", "lt_runtime_camera_info", "lt_runtime_camera_flush"):
        require(function in header and function in source, f"{function} is not declared and defined")
    require('dlsym(library, "qemu_ios_camera_frame")' in source,
            "the portable layer does not resolve the engine's camera entry point")


def android_asks_for_the_permission():
    manifest = read("Android/app/src/main/AndroidManifest.xml")
    require('android.permission.CAMERA' in manifest, "the manifest does not ask for the camera")
    activity = read("Android/app/src/main/java/org/un1box/android/MainActivity.kt")
    require("Manifest.permission.CAMERA" in activity, "the switch does not ask Android for the camera")


def android_the_switch_reaches_the_worker():
    service = read("Android/app/src/main/java/org/un1box/android/EngineService.kt")
    require('getBoolean("camera", false)' in service, "the worker does not read the camera switch")
    require('setenv("IT_CAMERA"' in service,
            "IT_CAMERA is not set before the guest starts: off, the board unmatches the isp node and there is "
            "no camera at all")
    require("CameraCapture(" in service and "startCamera()" in service, "the worker never starts the capture")
    require("follow(Native.cameraSensor())" in service,
            "the capture does not follow the guest's own switch between cameras")


def android_the_switch_is_in_settings():
    settings = read("Android/app/src/main/java/org/un1box/android/SettingsScreen.kt")
    require('"camera_toggle"' in settings, "Settings has no camera switch")
    require("host.cameraStatus" in settings, "Settings does not show what the camera is doing")
    shell = read("Android/app/src/main/java/org/un1box/android/Shell.kt")
    require("val camera: Boolean" in shell and "val cameraStatus: String" in shell,
            "the screen host does not carry the camera's state")


# --- the stack itself --------------------------------------------------------

def patch_stack(patch_dir, engine):
    """The whole stack applies to a clean engine and commits to it."""
    if engine is None:
        raise AssertionError("skipped: no engine checkout")
    scratch = Path(tempfile.mkdtemp())
    tree = scratch / "engine"
    try:
        archive = subprocess.run(["git", "-C", str(engine), "archive", "HEAD"], capture_output=True)
        if archive.returncode:
            raise AssertionError(f"git archive failed in {engine}")
        tree.mkdir()
        subprocess.run(["tar", "-x", "-C", str(tree)], input=archive.stdout, check=True)
        subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
        subprocess.run(["git", "add", "-A"], cwd=tree, check=True)
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=c", "commit", "-q", "-m", "base"],
                       cwd=tree, check=True)
        # CI commits the patches into the engine before these checks run, so a tree either has the whole stack
        # or none of it. A file only the camera patch adds is the marker.
        patched = (tree / "hw/arm/host-camera.c").is_file()
        if not patched:
            for patch in sorted(Path(patch_dir).glob("*.patch")):
                applied = subprocess.run(["git", "apply", str(patch)], cwd=tree,
                                         capture_output=True, text=True)
                if applied.returncode:
                    raise AssertionError(f"{patch.name} does not apply: {applied.stderr.strip()[:400]}")
        subprocess.run(["git", "add", "-A"], cwd=tree, check=True)
        # CI commits the patches into its checkout before these checks run, so a tree that already has them
        # has nothing to commit -- which is not a failure.
        staged = subprocess.run(["git", "status", "--porcelain"], cwd=tree, capture_output=True, text=True)
        if staged.stdout.strip():
            committed = subprocess.run(
                ["git", "-c", "user.email=a@b", "-c", "user.name=c",
                 "-c", "user.name=Un1box", "commit", "-q", "-m", "camera"],
                cwd=tree, capture_output=True, text=True)
            if committed.returncode:
                raise AssertionError(f"the stack does not commit: "
                                     f"{(committed.stderr or committed.stdout).strip()[:400]}")
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=tree, capture_output=True, text=True)
        if dirty.stdout.strip():
            raise AssertionError(f"the patched tree is dirty after the commit: {dirty.stdout.strip()[:300]}")
        return f"{len(list(Path(patch_dir).glob('*.patch')))} patches, clean"
    finally:
        import shutil
        shutil.rmtree(scratch, ignore_errors=True)


CHECKS = [
    ("engine patch: the hand-off and the API are there", engine_patch_ships_the_hand_off),
    ("engine patch: the guest's own switch picks the sensor", engine_patch_chooses_the_source_at_recognition),
    ("engine patch: the camera is drawn before the pattern", engine_patch_draws_the_camera_before_the_pattern),
    ("engine patch: the front sensor's picture is turned", engine_patch_turns_the_front_sensor_upright),
    ("engine patch: a size that is not a mode is refused", engine_patch_refuses_a_size_that_is_not_a_mode),
    ("portable: the camera API is declared and resolved", portable_declares_the_camera),
    ("native: every entry point the app declares exists", jni_matches_the_app),
    ("android: the manifest and the switch ask for the camera", android_asks_for_the_permission),
    ("android: the switch reaches the worker and the guest", android_the_switch_reaches_the_worker),
    ("android: Settings has the switch and the line", android_the_switch_is_in_settings),
]

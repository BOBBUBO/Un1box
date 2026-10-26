"""The connections between the camera's layers, checked as text.

The slices prove what each layer does. These checks prove the layers are wired
to each other and to the platform: the same four engine entry points are
exported, resolved and declared; the ISP asks the camera before it draws its
test pattern, which is the ordering the microphone got wrong and the reason a
fallback in front of a real source is invisible; the device's own camera switch
reaches the phone's camera; and the switch in Settings reaches the worker that
opens it, with the permission the manifest asks for.

Each check raises with one sentence when it fails, and returns a short note when
it passes.
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATCHES = ROOT / "ci" / "engine-patches"
CAMERA_PATCH = PATCHES / "0010-engine-camera-input.patch"

ENGINE_SYMBOLS = ("qemu_ios_camera_write", "qemu_ios_camera_flush", "qemu_ios_camera_state",
                  "qemu_ios_camera_sensor")
PORTABLE_SYMBOLS = ("lt_runtime_camera_write", "lt_runtime_camera_info", "lt_runtime_camera_flush",
                    "lt_runtime_camera_sensor")


def read(relative):
    path = ROOT / relative
    if not path.is_file():
        raise AssertionError(f"{relative} is missing")
    return path.read_text(errors="ignore")


def added_lines(patch):
    """The lines a patch adds, as one body of text."""
    return "\n".join(line[1:] for line in read(patch).splitlines() if line.startswith("+"))


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def file_body(patch, relative):
    """A patch's view of one file: the context and added lines, in order."""
    lines, inside = [], False
    for line in read(patch).splitlines():
        if line.startswith("diff --git "):
            inside = line.endswith(" b/" + relative)
            continue
        if inside and line[:1] in (" ", "+") and not line.startswith("+++"):
            lines.append(line[1:])
    return "\n".join(lines)


# --- the engine side, from the patch that ships it -------------------------

def engine_patch_adds_the_source():
    body = added_lines(CAMERA_PATCH)
    require("bool host_camera_push(" in body, "the patch no longer adds hw/arm/host-camera.c")
    require("const uint8_t *host_camera_take(" in body, "host-camera.c no longer hands a frame to the guest")
    require("g_renew(" in body,
            "host-camera.c must grow its buffers with g_renew(): the frame sizes change with the guest's mode")
    require("'host-camera.c'" in body, "hw/arm/meson.build does not build host-camera.c")


def engine_patch_asks_the_camera_first():
    """The ordering that made the microphone's first device build silent: the fallback must come second."""
    body = file_body(CAMERA_PATCH, "hw/arm/s5l8930_isp.c")
    camera = body.find("host_camera_take(")
    pattern = body.find("isp_pattern(s, w, h, nv12)")
    require(camera >= 0, "isp_frame() no longer asks the frontend's camera for a frame")
    require(pattern >= 0, "isp_frame() no longer has its test-pattern fallback")
    require(camera < pattern,
            "the frontend's camera must be asked BEFORE the test pattern: a fallback in front of the real "
            "source is the microphone's silence bug again")
    require("if (camera && cw && ch)" in body,
            "the camera's frame is not checked before use, so a take that failed would be drawn from NULL")


def engine_patch_exports_the_api():
    body = added_lines(CAMERA_PATCH)
    for symbol in ENGINE_SYMBOLS:
        require(re.search(r"\b" + symbol + r"\s*\(", body), f"the patch does not define {symbol}()")
    require("(2u << 16) | 5u" in body, "QEMU_IOS_API_VERSION is not bumped to 2.5")


def engine_patch_lets_the_guest_choose_the_sensor():
    """Both cameras, and the guest's own switch is what picks between them."""
    body = added_lines(CAMERA_PATCH)
    require("HOST_CAMERA_FRONT" in body, "the patch knows nothing about the front camera")
    recognized = file_body(CAMERA_PATCH, "hw/arm/s5l8930_isp.c")
    require("cat == 0x04" in recognized, "sensor recognition 04 (front) is not recognised")
    require("host_camera_select(s->sensor)" in recognized,
            "recognising a sensor does not tell the frontend which camera to stream")
    # The branch must fall through to the command loop below, which is what clears the register the
    # kext polls. A `return` here is the bug that makes sensor recognition read back non-zero forever.
    between = recognized.split("host_camera_select(s->sensor);", 1)[1].split("for (unsigned i = 0;", 1)[0]
    code = re.sub(r"/\*.*?\*/", "", between, flags=re.S)
    code = "\n".join(line for line in code.splitlines() if not line.strip().startswith(("*", "//")))
    require("return" not in code,
            "sensor recognition returns early, so the loop below never clears the register the kext polls")


def engine_patch_forgets_a_stopped_camera():
    extras = added_lines(CAMERA_PATCH)
    require("host_camera_flush();" in extras, "stopping the capture does not drop the frame the guest never took")


# --- portable ---------------------------------------------------------------

def portable_declares_the_api():
    header = read("Portable/include/lighttouch.h")
    for symbol in PORTABLE_SYMBOLS:
        require(re.search(r"\b" + symbol + r"\s*\(", header), f"lighttouch.h does not declare {symbol}()")
    require("#define LT_CAMERA_BACK 0" in header, "lighttouch.h does not define LT_CAMERA_BACK")
    require("#define LT_CAMERA_FRONT 1" in header, "lighttouch.h does not define LT_CAMERA_FRONT")
    require("#define LT_CAMERA_MAX_WIDTH 1280" in header and "#define LT_CAMERA_MAX_HEIGHT 960" in header,
            "lighttouch.h does not cap the camera frame's size, so the engine's buffers are unbounded")
    fields = re.search(r"typedef struct \{[^}]*\} lt_camera_info;", header, re.S)
    require(fields, "lighttouch.h has no lt_camera_info")
    order = [name for name in ("sensor", "width", "height", "pushed_frames", "taken_frames", "replaced_frames",
                               "rejected_frames") if name in fields.group(0)]
    require(len(order) == 7, f"lt_camera_info is missing counters: has {order}")


def portable_resolves_exactly_the_exported_names():
    runtime = read("Portable/src/Runtime.cpp")
    for symbol in ENGINE_SYMBOLS:
        require(f'dlsym(library, "{symbol}")' in runtime, f"Runtime.cpp never resolves {symbol}")
    require("engine has no camera input" in runtime,
            "the optional camera symbols are not treated as optional: an older engine would fail to load")


# --- android ----------------------------------------------------------------

def android_asks_for_the_permission():
    manifest = read("Android/app/src/main/AndroidManifest.xml")
    require("android.permission.CAMERA" in manifest, "AndroidManifest.xml does not declare the CAMERA permission")
    require('android:name="android.hardware.camera" android:required="false"' in manifest,
            "a phone without a camera would not be able to install the app")


def android_capture_matches_the_api():
    capture = read("Android/app/src/main/java/org/lighttouch/android/CameraCapture.kt")
    require("ImageFormat.YUV_420_888" in capture, "the capture does not read YUV_420_888")
    require("ImageReader.newInstance" in capture, "the capture has no image reader")
    require("CameraDevice.TEMPLATE_PREVIEW" in capture, "the capture does not use the preview template")
    require("CONTROL_AF_MODE_CONTINUOUS_PICTURE" in capture, "the camera does not autofocus")
    require("Native.cameraFrame(" in capture, "the capture never hands a frame to the engine")
    require("Native.cameraFlush()" in capture, "stopping the capture does not drop the guest's pending frame")
    require("rowStride" in capture and "pixelStride" in capture,
            "the capture does not pass the device's own strides, so the picture would be sheared")
    require('HandlerThread("camera")' in capture, "the camera has no thread of its own")
    require("LENS_FACING_FRONT" in capture and "LENS_FACING_BACK" in capture,
            "the capture does not know both cameras")
    require("acquireLatestImage()" in capture,
            "the capture does not acquire the newest image, so backpressure would build a backlog")


def android_follows_the_guests_camera_switch():
    service = read("Android/app/src/main/java/org/lighttouch/android/EngineService.kt")
    require("Native.cameraSensor()" in service,
            "the worker never asks the engine which sensor the guest recognised, so the front camera would "
            "never be the one streamed")
    require('"camera-watch"' in service, "nothing follows the guest's camera switch while it runs")
    require("CameraCapture.Choice.FOLLOW" in service, "the Follow choice is not honoured")


def android_switch_reaches_the_worker():
    settings = read("Android/app/src/main/java/org/lighttouch/android/AppSettings.kt")
    require('putBoolean("camera"' in settings, "the switch no longer persists its value")
    require('putString("camera_facing"' in settings, "the camera choice is not persisted")
    service = read("Android/app/src/main/java/org/lighttouch/android/EngineService.kt")
    require('getBoolean("camera"' in service,
            "the worker reads the switch from a different store than the settings write it to")
    require('getString("camera_facing"' in service,
            "the worker reads the camera choice from a different store than the settings write it to")
    activity = read("Android/app/src/main/java/org/lighttouch/android/MainActivity.kt")
    require("permission.CAMERA" in activity and "cameraPermission.launch" in activity,
            "turning the switch on does not ask Android for the camera permission")
    screen = read("Android/app/src/main/java/org/lighttouch/android/SettingsScreen.kt")
    require("host.useCamera(" in screen, "the camera switch is not wired to the host")
    require("host.useCameraFacing(" in screen, "the camera choice is not wired to the host")


def android_jni_names_match():
    native = read("Android/app/src/main/java/org/lighttouch/android/Native.kt")
    jni = read("Android/app/src/main/cpp/lighttouch_jni.cpp")
    declared = re.findall(r"external fun (\w+)\(", native)
    missing = [name for name in declared
               if not re.search(r"\bJava_org_lighttouch_android_Native_" + name + r"\s*\(", jni)]
    require(not missing, f"Native.kt declares externals the JNI never defines: {missing}")
    for name in ("cameraFrame", "cameraFlush", "cameraInfo", "cameraSensor"):
        require(name in declared, f"Native.kt does not declare {name}()")


# --- the patch stack --------------------------------------------------------

def patch_stack(patch_dir, engine):
    """Apply ci/engine-patches to the engine and commit them as CI does."""
    if engine is None:
        return "skipped: no engine checkout"
    engine = Path(engine)
    inside = subprocess.run(["git", "-C", str(engine), "rev-parse", "--git-dir"],
                            capture_output=True, text=True)
    if inside.returncode:
        return "skipped: the engine checkout is not a git repository"
    revision = subprocess.run(["git", "-C", str(engine), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
    scratch = Path(tempfile.mkdtemp(prefix="lt-camera-stack-"))
    try:
        tree = scratch / "engine"
        tree.mkdir()
        archive = subprocess.run(["git", "-C", str(engine), "archive", "HEAD"], capture_output=True)
        if archive.returncode:
            raise AssertionError(f"git archive failed in {engine}")
        subprocess.run(["tar", "-x", "-C", str(tree)], input=archive.stdout, check=True)
        already = "host_camera_take" in (tree / "hw/arm/s5l8930_isp.c").read_text(errors="ignore")
        if not already:
            for patch in sorted(Path(patch_dir).glob("*.patch")):
                applied = subprocess.run(["git", "apply", str(patch)], cwd=tree, capture_output=True, text=True)
                if applied.returncode:
                    raise AssertionError(f"{patch.name} does not apply to {revision}: {applied.stderr.strip()}")
        for command in (["git", "init", "-q"], ["git", "add", "-A"]):
            subprocess.run(command, cwd=tree, check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.name=camera-selftest", "-c", "user.email=camera@selftest",
                        "commit", "-q", "-m", "engine patches"],
                       cwd=tree, check=True, capture_output=True,
                       env={**os.environ, "GIT_AUTHOR_DATE": "2001-01-01T00:00:00Z",
                            "GIT_COMMITTER_DATE": "2001-01-01T00:00:00Z"})
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=tree, capture_output=True, text=True)
        if dirty.stdout.strip():
            raise AssertionError(f"the patched engine tree is dirty after the commit: {dirty.stdout.strip()[:400]}")
        stamp = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=tree,
                               capture_output=True, text=True).stdout.strip()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)  # a .git tree does not always delete cleanly
    return f"from {revision}: {len(list(Path(patch_dir).glob('*.patch')))} patches, clean at {stamp}"


CHECKS = [
    ("engine patch: the device code, its header and its build entry", engine_patch_adds_the_source),
    ("engine patch: the camera is asked before the test pattern", engine_patch_asks_the_camera_first),
    ("engine patch: the four entry points and API 2.5", engine_patch_exports_the_api),
    ("engine patch: the guest's switch picks the sensor, both exist", engine_patch_lets_the_guest_choose_the_sensor),
    ("engine patch: a stopped camera is forgotten", engine_patch_forgets_a_stopped_camera),
    ("portable: the API is declared and typed", portable_declares_the_api),
    ("portable: the engine's entry points are resolved, and optional", portable_resolves_exactly_the_exported_names),
    ("android: the manifest asks for the camera", android_asks_for_the_permission),
    ("android: the capture reads YUV_420_888 with the device's strides", android_capture_matches_the_api),
    ("android: the front camera follows the guest's switch", android_follows_the_guests_camera_switch),
    ("android: the switch and the choice reach the worker", android_switch_reaches_the_worker),
    ("android: Kotlin externals all have JNI bodies", android_jni_names_match),
]

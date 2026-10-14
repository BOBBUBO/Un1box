#include "lighttouch.h"
#include <jni.h>
#include <android/native_window_jni.h>
#include <android/bitmap.h>
#include <algorithm>
#include <chrono>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#include <time.h>
#include <sys/stat.h>

namespace {
std::mutex guard;
lt_runtime *runtime = nullptr;
lt_device *device = nullptr;
ANativeWindow *window = nullptr;
std::vector<uint8_t> pixels(LT_FRAME_CAPACITY);
lt_frame_info frame = LT_INIT(lt_frame_info);
lt_pcm_info pcm = LT_INIT(lt_pcm_info);
lt_board_info board = LT_INIT(lt_board_info);
uint64_t sequence = 0;
bool presenter_started = false;
std::string diagnostic;
struct Utf {
    JNIEnv *env; jstring source; const char *value;
    Utf(JNIEnv *e, jstring s): env(e), source(s), value(s ? e->GetStringUTFChars(s, nullptr) : nullptr) {}
    ~Utf() { if (value) env->ReleaseStringUTFChars(source, value); }
};
jstring string(JNIEnv *e, const std::string &s) { return e->NewStringUTF(s.c_str()); }
std::string result(lt_status status, const lt_error &error) {
    return status == LT_OK || status == LT_AGAIN ? "" : (error.message[0] ? error.message : "Native operation failed");
}
struct Viewport { double left, top, width, height; };
Viewport viewport(int width, int height) {
    const double scale = std::min(double(width) / std::max(frame.width, 1u), double(height) / std::max(frame.height, 1u));
    double w = frame.width * scale, h = frame.height * scale;
    return {(width-w)/2, (height-h)/2, w, h};
}
void present() {
    for (;;) {
        {
            std::lock_guard<std::mutex> lock(guard);
            if (runtime && window) {
                lt_error error = LT_INIT(lt_error);
                lt_status status = lt_runtime_copy_frame(runtime, pixels.data(), pixels.size(), &sequence, &frame, &error);
                if (status == LT_OK && frame.width && frame.height && frame.stride >= frame.width * 4 && frame.bytes <= pixels.size()) {
                    ANativeWindow_Buffer target;
                    if (ANativeWindow_lock(window, &target, nullptr) == 0) {
                        const auto view = viewport(target.width, target.height);
                        for (int y=0; y<target.height; ++y) {
                            auto *row = static_cast<uint32_t *>(target.bits) + y * target.stride;
                            std::fill(row, row+target.width, 0xff000000u);
                            if (y<view.top || y>=view.top+view.height) continue;
                            uint32_t sy=std::min(uint32_t((y-view.top)*frame.height/view.height), frame.height-1);
                            for (int x=int(view.left); x<view.left+view.width && x<target.width; ++x) {
                                uint32_t sx=std::min(uint32_t((x-view.left)*frame.width/view.width), frame.width-1);
                                const uint8_t *p=pixels.data()+sy*frame.stride+sx*4;
                                row[x] = uint32_t(p[2]) | (uint32_t(p[1])<<8) | (uint32_t(p[0])<<16) | 0xff000000u;
                            }
                        }
                        ANativeWindow_unlockAndPost(window);
                    }
                }
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(16));
    }
}
}
#define JNI(name) extern "C" JNIEXPORT
JNI(importBase) jstring JNICALL Java_org_lighttouch_android_Native_importBase(JNIEnv *env, jobject, jstring source, jstring destination) {
    Utf src(env, source), dst(env, destination);
    lt_import_config config = LT_INIT(lt_import_config);
    config.source=src.value; config.destination=dst.value; config.max_bytes=64ull*1024*1024*1024;
    lt_error error=LT_INIT(lt_error);
    struct stat info;
    if(stat(src.value,&info)!=0)return string(env,"Cannot inspect imported base");
    if(S_ISREG(info.st_mode)) {
        char checksum[65];auto status=lt_sha256_file(src.value,checksum,&error);
        if(status!=LT_OK)return string(env,result(status,error));
        config.expected_sha256=checksum;
        return string(env,result(lt_import_archive(&config,&error),error));
    }
    return string(env,result(lt_import_directory(&config,&error),error));
}
JNI(start) jstring JNICALL Java_org_lighttouch_android_Native_start(JNIEnv *env, jobject, jstring base) {
    std::lock_guard<std::mutex> lock(guard);
    if (runtime) return string(env,"Worker already consumed; stop and start a fresh worker");
    Utf path(env,base);
    if (!path.value) return string(env,"Missing base path");
    std::string root=path.value; root=root.substr(0,root.find_last_of('/'));
    std::string state=root+"/state", rom=root+"/bootrom.bin", guest=root+"/guest.itpack", serial=root+"/guest-serial.log";
    lt_device_config config=LT_INIT(lt_device_config);
    config.base_path=path.value;config.state_path=state.c_str(); config.allow_dirty=1;
    lt_error error=LT_INIT(lt_error);
    auto status=lt_device_open(&config,&device,&error);
    if (status!=LT_OK) { diagnostic=result(status,error);return string(env,diagnostic); }
    lt_device_info info=LT_INIT(lt_device_info);
    lt_device_describe(device,&info,&error);
    lt_engine_board("libqemu-arm.so",info.board,&board,&error);
    lt_runtime_config engine=LT_INIT(lt_runtime_config);
    engine.engine_library="libqemu-arm.so";engine.device=device;
    engine.boot=lt_boot_config LT_INIT(lt_boot_config);
    engine.boot.machine=nullptr;engine.boot.bootrom_path=rom.c_str();engine.boot.guest_package_path=guest.c_str();
    engine.boot.serial_path=serial.c_str();engine.boot.audio_enabled=1;
    status=lt_runtime_create(&engine,&runtime,&error);
    if (status==LT_OK) status=lt_runtime_start(runtime,&error);
    if (status!=LT_OK) { diagnostic=result(status,error); return string(env,diagnostic); }
    if (!presenter_started) { presenter_started=true;std::thread(present).detach(); }
    return string(env,"");
}
JNI(status) jobjectArray JNICALL Java_org_lighttouch_android_Native_status(JNIEnv *env,jobject) {
    std::lock_guard<std::mutex> lock(guard);
    const char *states[]={"stopped","starting","running","paused","stopping","failed"};
    lt_runtime_status s=LT_INIT(lt_runtime_status);lt_error error=LT_INIT(lt_error);
    std::string detail=diagnostic;
    if(runtime) { auto status=lt_runtime_poll(runtime,&s,&error);if(status!=LT_OK) detail=result(status,error);
        else detail="Guest gyro "+std::string((board.capabilities & 2)?"supported":"unavailable")+"; contacts "+std::to_string(board.max_contacts)+"; frames "+std::to_string(s.frames)+"; "+(s.dirty?"writable state dirty":"writable state clean")+"; "+(s.guest_shutdown_confirmed?"guest shutdown confirmed":"shutdown unconfirmed"); }
    auto arr=env->NewObjectArray(2,env->FindClass("java/lang/String"),nullptr);
    env->SetObjectArrayElement(arr,0,string(env,states[!diagnostic.empty() && !runtime ? 5u : std::min(s.state,5u)]));
    env->SetObjectArrayElement(arr,1,string(env,detail));return arr;
}
JNI(surface) void JNICALL Java_org_lighttouch_android_Native_surface(JNIEnv *env,jobject,jobject surface) {
    std::lock_guard<std::mutex> lock(guard);
    if(window) ANativeWindow_release(window);
    window=surface?ANativeWindow_fromSurface(env,surface):nullptr;
    if(window) { ANativeWindow_setBuffersGeometry(window,0,0,WINDOW_FORMAT_RGBA_8888);sequence=0; }
}
JNI(pause) jstring JNICALL Java_org_lighttouch_android_Native_pause(JNIEnv *env,jobject,jboolean paused) {
    std::lock_guard<std::mutex> lock(guard);lt_error error=LT_INIT(lt_error);
    return string(env,runtime?result(lt_runtime_pause(runtime,paused,&error),error):"No active session");
}
JNI(stop) jstring JNICALL Java_org_lighttouch_android_Native_stop(JNIEnv *env,jobject) {
    std::lock_guard<std::mutex> lock(guard);lt_error error=LT_INIT(lt_error);
    return string(env,runtime?result(lt_runtime_request_shutdown(runtime,&error),error):"");
}
JNI(button) jstring JNICALL Java_org_lighttouch_android_Native_button(JNIEnv *env,jobject,jint button,jboolean down) {
    std::lock_guard<std::mutex> lock(guard);lt_error error=LT_INIT(lt_error);
    return string(env,runtime?result(lt_runtime_button(runtime,button,down,&error),error):"No active session");
}
JNI(cancelInput) void JNICALL Java_org_lighttouch_android_Native_cancelInput(JNIEnv *,jobject) {
    std::lock_guard<std::mutex> lock(guard);lt_error error=LT_INIT(lt_error);if(runtime) lt_runtime_cancel_input(runtime,&error);
}
JNI(touch) jstring JNICALL Java_org_lighttouch_android_Native_touch(JNIEnv *env,jobject,jlong time,jintArray ids,jintArray phases,jfloatArray xy,jint width,jint height,jint rotation) {
    std::lock_guard<std::mutex> lock(guard);
    const jsize count=env->GetArrayLength(ids);
    if(count<1 || count>LT_MAX_CONTACTS || env->GetArrayLength(phases)!=count || env->GetArrayLength(xy)!=count*2) return string(env,"Invalid contact batch");
    if(!runtime || !frame.width || !frame.height) return string(env,"");
    jint id[LT_MAX_CONTACTS], phase[LT_MAX_CONTACTS];jfloat coords[LT_MAX_CONTACTS*2];
    env->GetIntArrayRegion(ids,0,count,id);env->GetIntArrayRegion(phases,0,count,phase);env->GetFloatArrayRegion(xy,0,count*2,coords);
    lt_touch_batch batch=LT_INIT(lt_touch_batch);batch.timestamp_ns=time;batch.count=count;
    const auto view=viewport(width,height);lt_error error=LT_INIT(lt_error);
    for(jsize i=0;i<count;++i) {
        batch.contacts[i]=lt_contact LT_INIT(lt_contact);batch.contacts[i].id=id[i];batch.contacts[i].phase=phase[i];
        auto status=lt_map_contact(coords[i*2],coords[i*2+1],view.left,view.top,view.width,view.height,0,&batch.contacts[i].x,&batch.contacts[i].y);
        if(status!=LT_OK) { lt_runtime_cancel_input(runtime,&error);return string(env,""); }
    }
    // Guest portrait is kept upright within Android's rotated view. Mapping receives
    // view-local pixels; applying display rotation again would rotate contacts twice.
    (void)rotation;
    return string(env,result(lt_runtime_touch(runtime,&batch,&error),error));
}
JNI(motion) jstring JNICALL Java_org_lighttouch_android_Native_motion(JNIEnv *env,jobject,jint kind,jlong time,jfloatArray xyz,jint rotation) {
    std::lock_guard<std::mutex> lock(guard);if(!runtime)return string(env,"");
    if(env->GetArrayLength(xyz)!=3)return string(env,"Invalid sensor vector");
    jfloat v[3];env->GetFloatArrayRegion(xyz,0,3,v);
    lt_motion sample=LT_INIT(lt_motion);sample.timestamp_ns=time;sample.kinds=kind;
    double *target=kind==2?sample.angular_velocity:sample.acceleration;
    // Android SensorManager is in natural device axes; shared domain rotates into guest axes.
    for(int i=0;i<3;++i)target[i]=v[i];
    timespec now;clock_gettime(CLOCK_BOOTTIME,&now);
    lt_motion portrait=LT_INIT(lt_motion);lt_error error=LT_INIT(lt_error);
    auto status=lt_map_motion(rotation,time,uint64_t(now.tv_sec)*1000000000+now.tv_nsec,&sample,&portrait,&error);
    if(status==LT_OK)status=lt_runtime_motion(runtime,&portrait,&error);
    // An unavailable guest gyro is a capability, never fabricated as acceleration.
    return string(env,status==LT_UNSUPPORTED?"":result(status,error));
}
JNI(capture) jobject JNICALL Java_org_lighttouch_android_Native_capture(JNIEnv *env,jobject) {
    std::lock_guard<std::mutex> lock(guard);if(!frame.width||!frame.height)return nullptr;
    jclass bitmap=env->FindClass("android/graphics/Bitmap"),config=env->FindClass("android/graphics/Bitmap$Config");
    auto argb=env->GetStaticObjectField(config,env->GetStaticFieldID(config,"ARGB_8888","Landroid/graphics/Bitmap$Config;"));
    auto create=env->GetStaticMethodID(bitmap,"createBitmap","(IILandroid/graphics/Bitmap$Config;)Landroid/graphics/Bitmap;");
    jobject result=env->CallStaticObjectMethod(bitmap,create,jint(frame.width),jint(frame.height),argb);
    if(env->ExceptionCheck()||!result)return nullptr;
    AndroidBitmapInfo info;void *data=nullptr;
    if(AndroidBitmap_getInfo(env,result,&info)!=ANDROID_BITMAP_RESULT_SUCCESS||AndroidBitmap_lockPixels(env,result,&data)!=ANDROID_BITMAP_RESULT_SUCCESS)return nullptr;
    for(uint32_t y=0;y<frame.height;++y)for(uint32_t x=0;x<frame.width;++x) {
        const uint8_t *src=pixels.data()+y*frame.stride+x*4;auto *dst=static_cast<uint8_t *>(data)+y*info.stride+x*4;
        dst[0]=src[2];dst[1]=src[1];dst[2]=src[0];dst[3]=255;
    }
    AndroidBitmap_unlockPixels(env,result);return result;
}
JNI(audio) jint JNICALL Java_org_lighttouch_android_Native_audio(JNIEnv *env,jobject,jshortArray buffer) {
    std::lock_guard<std::mutex> lock(guard);if(!runtime)return 0;
    std::vector<int16_t> data(env->GetArrayLength(buffer));lt_error error=LT_INIT(lt_error);
    const auto status=lt_runtime_pcm(runtime,data.data(),uint32_t(data.size()/2),&pcm,&error);
    if(status!=LT_OK||pcm.channels>2||pcm.channels<1||pcm.frames*pcm.channels>data.size())return 0;
    const jint count=pcm.frames*pcm.channels;env->SetShortArrayRegion(buffer,0,count,data.data());return count;
}
JNI(audioInfo) jintArray JNICALL Java_org_lighttouch_android_Native_audioInfo(JNIEnv *env,jobject) {
    std::lock_guard<std::mutex> lock(guard);jint data[2]={jint(pcm.sample_rate),jint(pcm.channels)};
    jintArray arr=env->NewIntArray(2);env->SetIntArrayRegion(arr,0,2,data);return arr;
}

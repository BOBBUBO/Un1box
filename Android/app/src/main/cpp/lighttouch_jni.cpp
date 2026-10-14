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
#include <unistd.h>

namespace {
std::mutex guard;
std::mutex library_guard;
lt_library *library=nullptr;
std::string library_root;
lt_runtime *runtime = nullptr;
lt_device *device = nullptr;
ANativeWindow *window = nullptr;
std::vector<uint8_t> pixels(LT_FRAME_CAPACITY);
lt_frame_info frame = LT_INIT(lt_frame_info);
lt_pcm_info pcm = LT_INIT(lt_pcm_info);
lt_board_info board = LT_INIT(lt_board_info);
uint64_t sequence = 0;
bool presenter_started = false;
bool starting = false, startup_stop_requested = false;
uint64_t last_rejected_input = 0;
std::string diagnostic;
struct Utf {
    JNIEnv *env; jstring source; const char *value;
    Utf(JNIEnv *e, jstring s): env(e), source(s), value(s ? e->GetStringUTFChars(s, nullptr) : nullptr) {}
    ~Utf() { if (value) env->ReleaseStringUTFChars(source, value); }
};
jstring string(JNIEnv *e, const std::string &s) { return e->NewStringUTF(s.c_str()); }
std::string result(lt_status status, const lt_error &error) {
    return status == LT_OK ? "" : (error.message[0] ? error.message : "Native operation failed");
}
struct Viewport { double left, top, width, height; };
Viewport viewport(int width, int height) {
    const double scale = std::min(double(width) / std::max(frame.width, 1u), double(height) / std::max(frame.height, 1u));
    double w = frame.width * scale, h = frame.height * scale;
    return {(width-w)/2, (height-h)/2, w, h};
}
lt_status open_library(const char *root,lt_error *error) {
    if(!root)return LT_INVALID;
    if(library && library_root==root)return LT_OK;
    if(library)lt_library_close(library);
    library=nullptr;library_root=root;
    lt_library_config config=LT_INIT(lt_library_config);config.root_path=root;
    auto status=lt_library_open(&config,&library,error);
    if(status!=LT_OK)return status;
    uint32_t count=0;status=lt_library_count(library,&count,error);
    if(status!=LT_OK)return status;
    std::string parent=library_root.substr(0,library_root.find_last_of('/'));
    std::string oldbase=parent+"/base",oldstate=parent+"/state",oldrom=parent+"/bootrom.bin",oldguest=parent+"/guest.itpack";
    if(count==0 && access(oldbase.c_str(),F_OK)==0) {
        lt_library_import_config migration=LT_INIT(lt_library_import_config);
        migration.source_path=oldbase.c_str();migration.display_name="Existing device";
        migration.bootrom_path=access(oldrom.c_str(),R_OK)==0?oldrom.c_str():nullptr;
        migration.guest_package_path=access(oldguest.c_str(),R_OK)==0?oldguest.c_str():nullptr;
        migration.max_bytes=64ull*1024*1024*1024;
        lt_record_info record=LT_INIT(lt_record_info);
        status=access(oldstate.c_str(),F_OK)==0?lt_library_adopt_legacy(library,&migration,oldstate.c_str(),&record,error):lt_library_import(library,&migration,&record,error);
    }
    return status;
}
void present() {
    for (;;) {
        {
            std::lock_guard<std::mutex> lock(guard);
            if (runtime) {
                lt_input_status delivered=LT_INIT(lt_input_status);lt_error error=LT_INIT(lt_error);
                if(lt_runtime_input_status(runtime,&delivered,&error)==LT_OK && delivered.state==3 && delivered.request_id!=last_rejected_input) {
                    last_rejected_input=delivered.request_id;
                    diagnostic="Guest rejected contact batch "+std::to_string(delivered.request_id)+" (engine result "+std::to_string(delivered.engine_result)+"); contact release requested";
                    if(lt_runtime_cancel_input(runtime,&error)!=LT_OK)diagnostic += "; input release failed: "+std::string(error.message);
                }
            }
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
JNI(importBase) jstring JNICALL Java_org_lighttouch_android_Native_importBase(JNIEnv *env,jobject,jstring source,jstring root,jstring name,jstring rom,jstring guest) {
    std::lock_guard<std::mutex> lock(library_guard);
    Utf src(env,source),path(env,root),label(env,name),bootrom(env,rom),package(env,guest);
    lt_error error=LT_INIT(lt_error);auto status=open_library(path.value,&error);
    if(status!=LT_OK)return string(env,result(status,error));
    lt_library_import_config config=LT_INIT(lt_library_import_config);
    config.source_path=src.value;config.display_name=label.value;
    config.bootrom_path=bootrom.value && access(bootrom.value,R_OK)==0?bootrom.value:nullptr;
    config.guest_package_path=package.value && access(package.value,R_OK)==0?package.value:nullptr;
    config.max_bytes=64ull*1024*1024*1024;
    struct stat info;if(!src.value||stat(src.value,&info)!=0)return string(env,"Cannot inspect imported base");
    char checksum[65];
    if(S_ISREG(info.st_mode)) {
        status=lt_sha256_file(src.value,checksum,&error);if(status!=LT_OK)return string(env,result(status,error));
        config.expected_sha256=checksum;
    }
    lt_record_info record=LT_INIT(lt_record_info);
    status=lt_library_import(library,&config,&record,&error);
    return string(env,result(status,error));
}
JNI(records) jobjectArray JNICALL Java_org_lighttouch_android_Native_records(JNIEnv *env,jobject,jstring root) {
    std::lock_guard<std::mutex> lock(library_guard);Utf path(env,root);lt_error error=LT_INIT(lt_error);
    auto status=open_library(path.value,&error);uint32_t count=0;
    if(status==LT_OK)status=lt_library_count(library,&count,&error);
    jclass strings=env->FindClass("[Ljava/lang/String;");jclass str=env->FindClass("java/lang/String");
    if(status!=LT_OK) {
        auto rows=env->NewObjectArray(1,strings,nullptr);auto row=env->NewObjectArray(2,str,nullptr);
        env->SetObjectArrayElement(row,0,string(env,""));env->SetObjectArrayElement(row,1,string(env,result(status,error)));
        env->SetObjectArrayElement(rows,0,row);return rows;
    }
    auto rows=env->NewObjectArray(count,strings,nullptr);
    for(uint32_t i=0;i<count;++i) {
        lt_record_info record=LT_INIT(lt_record_info);status=lt_library_record_at(library,i,&record,&error);
        auto row=env->NewObjectArray(9,str,nullptr);
        lt_board_info facts=LT_INIT(lt_board_info);
        if(status==LT_OK)status=lt_engine_board("libqemu-arm.so",record.board,&facts,&error);
        std::vector<std::string> values=status==LT_OK?std::vector<std::string>{record.id,record.display_name,record.board,record.build,std::to_string(record.selected),std::to_string(record.dirty),std::to_string(facts.max_contacts),(facts.capabilities&2)?"1":"0",(facts.capabilities&1)?"1":"0"}:std::vector<std::string>{"",result(status,error)};
        for(size_t index=0;index<values.size();++index)env->SetObjectArrayElement(row,index,string(env,values[index]));
        env->SetObjectArrayElement(rows,i,row);env->DeleteLocalRef(row);
    }
    return rows;
}
JNI(select) jstring JNICALL Java_org_lighttouch_android_Native_select(JNIEnv *env,jobject,jstring root,jstring id) {
    std::lock_guard<std::mutex> lock(library_guard);Utf path(env,root),record(env,id);lt_error error=LT_INIT(lt_error);
    auto status=open_library(path.value,&error);if(status==LT_OK)status=lt_library_select(library,record.value,&error);
    return string(env,result(status,error));
}
JNI(start) jstring JNICALL Java_org_lighttouch_android_Native_start(JNIEnv *env,jobject,jstring root,jstring id) {
    {
        std::lock_guard<std::mutex> lock(guard);
        if(runtime || starting)return string(env,"Worker already consumed; stop and start a fresh worker");
        starting=true;diagnostic.clear();
    }
    // Integrity admission can hash many gigabytes. It must not hold the frame,
    // status, input or lifecycle mutex while the library performs that work.
    std::lock_guard<std::mutex> storage_lock(library_guard);
    Utf path(env,root),record(env,id);lt_error error=LT_INIT(lt_error);
    lt_device *prepared_device=nullptr;lt_runtime *prepared_runtime=nullptr;
    lt_board_info prepared_board=LT_INIT(lt_board_info);
    auto status=open_library(path.value,&error);
    if(status==LT_OK)status=lt_library_select(library,record.value,&error);
    lt_record_info selected=LT_INIT(lt_record_info);
    if(status==LT_OK)status=lt_library_selected(library,&selected,&error);
    if(status==LT_OK)status=lt_library_device_open(library,record.value,1,&prepared_device,&error);
    if(status==LT_OK)status=lt_engine_board("libqemu-arm.so",selected.board,&prepared_board,&error);
    std::string serial=std::string(selected.state_path)+"/guest-serial.log";
    lt_runtime_config engine=LT_INIT(lt_runtime_config);
    engine.engine_library="libqemu-arm.so";engine.device=prepared_device;engine.boot=lt_boot_config LT_INIT(lt_boot_config);
    engine.boot.machine=nullptr;engine.boot.bootrom_path=selected.bootrom_path[0]?selected.bootrom_path:nullptr;
    engine.boot.guest_package_path=selected.guest_package_path;engine.boot.serial_path=serial.c_str();engine.boot.audio_enabled=1;
    if(status==LT_OK)status=lt_runtime_create(&engine,&prepared_runtime,&error);
    if(status==LT_OK)status=lt_runtime_start(prepared_runtime,&error);
    if(status!=LT_OK) {
        bool released=!prepared_runtime;
        if(prepared_runtime){lt_error cleanup=LT_INIT(lt_error);released=lt_runtime_destroy(prepared_runtime,&cleanup)==LT_OK;}
        if(released && prepared_device)lt_device_close(prepared_device);
        std::lock_guard<std::mutex> lock(guard);starting=false;diagnostic=result(status,error);
        return string(env,diagnostic);
    }
    std::lock_guard<std::mutex> lock(guard);
    runtime=prepared_runtime;device=prepared_device;board=prepared_board;starting=false;
    if(startup_stop_requested) {
        lt_error stop_error=LT_INIT(lt_error);
        auto stopped=lt_runtime_request_shutdown(runtime,&stop_error);
        if(stopped!=LT_OK)diagnostic=result(stopped,stop_error);
    }
    if(!presenter_started){presenter_started=true;std::thread(present).detach();}
    return string(env,diagnostic);
}

JNI(status) jobjectArray JNICALL Java_org_lighttouch_android_Native_status(JNIEnv *env,jobject) {
    std::lock_guard<std::mutex> lock(guard);
    const char *states[]={"stopped","starting","running","paused","stopping","failed"};
    lt_runtime_status s=LT_INIT(lt_runtime_status);lt_error error=LT_INIT(lt_error);
    std::string detail=diagnostic;
    if(runtime) { auto status=lt_runtime_poll(runtime,&s,&error);if(status!=LT_OK) { diagnostic=result(status,error);detail=diagnostic; }
        else detail="Gyro deferred/unqualified "+std::string((board.capabilities & 2)?"(modeled)":"(not modeled)")+"; contacts "+std::to_string(board.max_contacts)+"; frames "+std::to_string(s.frames)+"; "+(s.dirty?"writable state dirty":"writable state clean")+"; "+(s.guest_shutdown_confirmed?"guest shutdown confirmed":"shutdown unconfirmed"); }
    auto arr=env->NewObjectArray(3,env->FindClass("java/lang/String"),nullptr);
    env->SetObjectArrayElement(arr,0,string(env,states[starting ? (startup_stop_requested ? 4u : 1u) : (!diagnostic.empty() && !runtime ? 5u : std::min(s.state,5u))]));
    env->SetObjectArrayElement(arr,1,string(env,detail));
    env->SetObjectArrayElement(arr,2,string(env,diagnostic));return arr;
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
    if(starting)startup_stop_requested=true;
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
    if(count<1 || count>jsize(LT_MAX_CONTACTS) || env->GetArrayLength(phases)!=count || env->GetArrayLength(xy)!=count*2) return string(env,"Invalid contact batch");
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
    auto status=lt_runtime_touch(runtime,&batch,&error);
    std::string failure=result(status,error);
    if(status!=LT_OK) {
        lt_error cancelled=LT_INIT(lt_error);
        const auto cancellation=lt_runtime_cancel_input(runtime,&cancelled);
        if(cancellation!=LT_OK)failure += "; input release failed: "+result(cancellation,cancelled);
    }
    if(!failure.empty())diagnostic=failure;
    return string(env,failure);
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
    // Dropping a stale/rate-limited live motion sample is safe; held contacts and
    // lifecycle admissions never use this policy.
    return string(env,status==LT_UNSUPPORTED || status==LT_AGAIN?"":result(status,error));
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

JNI(guestRequest) jobjectArray JNICALL Java_org_lighttouch_android_Native_guestRequest(JNIEnv *env,jobject,jint operation,jstring argument,jint timeout) {
    std::lock_guard<std::mutex> lock(guard);Utf text(env,argument);lt_error error=LT_INIT(lt_error);uint64_t id=0;
    lt_guest_request request=LT_INIT(lt_guest_request);request.operation=operation;request.timeout_ms=timeout;request.argument=text.value;
    auto status=runtime?lt_runtime_guest_request(runtime,&request,&id,&error):LT_AGAIN;
    auto output=env->NewObjectArray(2,env->FindClass("java/lang/String"),nullptr);
    env->SetObjectArrayElement(output,0,string(env,std::to_string(id)));env->SetObjectArrayElement(output,1,string(env,result(status,error)));return output;
}
JNI(guestPoll) jobjectArray JNICALL Java_org_lighttouch_android_Native_guestPoll(JNIEnv *env,jobject,jlong id) {
    std::lock_guard<std::mutex> lock(guard);lt_error error=LT_INIT(lt_error);lt_guest_result completed=LT_INIT(lt_guest_result);
    auto status=runtime?lt_runtime_guest_poll(runtime,id,&completed,&error):LT_AGAIN;
    auto output=env->NewObjectArray(4,env->FindClass("java/lang/String"),nullptr);
    std::string values[]={std::to_string(completed.state),std::to_string(completed.guest_status),status==LT_OK?std::string(completed.output,completed.bytes):"",result(status,error)};
    for(int i=0;i<4;++i)env->SetObjectArrayElement(output,i,string(env,values[i]));return output;
}
JNI(guestCancel) void JNICALL Java_org_lighttouch_android_Native_guestCancel(JNIEnv *,jobject,jlong id) {
    std::lock_guard<std::mutex> lock(guard);lt_error error=LT_INIT(lt_error);if(runtime)lt_runtime_guest_cancel(runtime,id,&error);
}

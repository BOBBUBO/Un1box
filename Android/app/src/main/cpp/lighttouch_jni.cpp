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
#include <fcntl.h>
#include <cstdio>
#include <cerrno>
#include <cstdlib>
#include <dlfcn.h>
#include <optional>
#include "presentation_lifetime.h"

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
uint64_t presentation_epoch = 0, surface_request = 0;
uint64_t presented_frames=0,first_present_ns=0,last_present_ns=0,previous_present_ns=0,max_present_gap_ns=0;
uint64_t max_present_work_ns=0,window_failures=0,frame_copy_failures=0,surface_generation=0,pcm_failures=0;
uint64_t present_intervals[6]={};
uint64_t frame_sample_pixels=0,frame_nonzero_rgb_samples=0,frame_rgb_sample_sum=0;
int first_lock_error=0,last_lock_error=0,first_post_error=0,last_post_error=0,last_geometry_error=0;
int last_buffer_format=0,last_buffer_width=0,last_buffer_height=0,last_buffer_stride=0;
uint64_t geometry_failures=0,invalid_buffers=0,first_window_error_ns=0;
unsigned window_log_budget=8;
uint64_t monotonic_ns() { timespec value{};clock_gettime(CLOCK_MONOTONIC,&value);return uint64_t(value.tv_sec)*1000000000ull+value.tv_nsec; }
bool presenter_started = false;
bool starting = false, startup_stop_requested = false, startup_pause_requested = false;
uint64_t last_rejected_input = 0;
std::string diagnostic;
unsigned graphics_provider_choice=0;
bool graphics_provider_fixed=false;
std::string graphics_provider_failure;
void *angle_provider_handles[3]={};
const char *graphics_provider_name() { return graphics_provider_choice==1?"angle-gl":"platform"; }
std::string engine_log_path;
std::string debug_qmp_path;
bool debug_qmp_armed=false;
bool debug_touch_trace_armed=false,debug_touch_trace_closed=false,debug_touch_trace_configured=false;
#ifdef LT_ANDROID_DEBUG
extern "C" int lighttouch_debug_qmp_snapshot(const char *,int);
#endif
void window_error(const char *operation,int code) {
    if(!first_window_error_ns)first_window_error_ns=monotonic_ns();
    if(window_log_budget) {
        --window_log_budget;
        std::fprintf(stderr,"LightTouch Surface %s error=%d generation=%llu format=%d width=%d height=%d stride=%d\n",operation,code,
            static_cast<unsigned long long>(surface_generation),last_buffer_format,last_buffer_width,last_buffer_height,last_buffer_stride);
    }
}
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
Viewport viewport(int width, int height, const lt_frame_info &source) {
    const double scale = std::min(double(width) / std::max(source.width, 1u), double(height) / std::max(source.height, 1u));
    double w = source.width * scale, h = source.height * scale;
    return {(width-w)/2, (height-h)/2, w, h};
}
Viewport viewport(int width, int height) { return viewport(width, height, frame); }
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
using WindowSnapshot = PresentationWindow<ANativeWindow, ANativeWindow_acquire, ANativeWindow_release>;
using WindowPost = PresentationPost<ANativeWindow, ANativeWindow_unlockAndPost>;
void present() {
    uint64_t configured_generation = 0;
    for (;;) {
        // The snapshot outlives both guarded scopes, so its release is outside
        // the control mutex even when a stale platform operation returns late.
        std::optional<WindowSnapshot> snapshot;
        lt_frame_info source = LT_INIT(lt_frame_info);
        uint64_t begin = 0;
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
                    begin=monotonic_ns();
                    source=frame;
                    snapshot.emplace(window, surface_generation, presentation_epoch);
                    frame_sample_pixels=frame_nonzero_rgb_samples=frame_rgb_sample_sum=0;
                    // A fixed 16x16 grid diagnoses the copied guest frame without
                    // another full image pass or claiming every pixel is black.
                    for(uint32_t gy=0;gy<16;++gy)for(uint32_t gx=0;gx<16;++gx) {
                        const uint32_t x=std::min((gx*2+1)*source.width/32,source.width-1);
                        const uint32_t y=std::min((gy*2+1)*source.height/32,source.height-1);
                        const uint8_t *sample=pixels.data()+y*source.stride+x*4;
                        const uint32_t rgb=uint32_t(sample[0])+sample[1]+sample[2];
                        ++frame_sample_pixels;frame_nonzero_rgb_samples+=rgb!=0;frame_rgb_sample_sum+=rgb;
                    }
                } else if(status!=LT_AGAIN) ++frame_copy_failures;
            }
        }
        if (snapshot) {
            ANativeWindow *target_window=snapshot->get();
            int geometry_code=0, geometry_width=0, geometry_height=0, geometry_format=0;
            const bool configure=configured_generation!=snapshot->generation();
            if(configure) {
                // Only this presenter configures or draws the window. Binder
                // setters only exchange the owning reference under guard.
                geometry_width=ANativeWindow_getWidth(target_window);
                geometry_height=ANativeWindow_getHeight(target_window);
                geometry_format=ANativeWindow_getFormat(target_window);
                geometry_code=ANativeWindow_setBuffersGeometry(target_window,0,0,WINDOW_FORMAT_RGBA_8888);
                configured_generation=snapshot->generation();
            }
            ANativeWindow_Buffer target{};
            const int lock_code=ANativeWindow_lock(target_window,&target,nullptr);
            int post_code=0;
            bool valid_buffer=false;
            if(lock_code==0) {
                WindowPost post(target_window);
                valid_buffer=target.bits && target.width>0 && target.height>0 && target.stride>=target.width &&
                    (target.format==WINDOW_FORMAT_RGBA_8888 || target.format==WINDOW_FORMAT_RGBX_8888);
                if(valid_buffer) {
                    const auto view=viewport(target.width,target.height,source);
                    // This thread is the sole pixels/frame writer and never
                    // resizes pixels. Capture reads under guard. Those concurrent
                    // reads are safe; the next write waits until this iteration
                    // and any guarded capture have finished. No runtime escapes.
                    for(int y=0;y<target.height;++y) {
                        auto *row=static_cast<uint32_t *>(target.bits)+y*target.stride;
                        std::fill(row,row+target.width,0xff000000u);
                        if(y<view.top || y>=view.top+view.height)continue;
                        uint32_t sy=std::min(uint32_t((y-view.top)*source.height/view.height),source.height-1);
                        for(int x=int(view.left);x<view.left+view.width && x<target.width;++x) {
                            uint32_t sx=std::min(uint32_t((x-view.left)*source.width/view.width),source.width-1);
                            const uint8_t *p=pixels.data()+sy*source.stride+sx*4;
                            row[x]=uint32_t(p[2])|(uint32_t(p[1])<<8)|(uint32_t(p[0])<<16)|0xff000000u;
                        }
                    }
                }
                // Exactly one post for every successful lock, including invalid
                // layouts and a window detached while the call was in flight.
                post_code=post.finish();
            }
            const auto now=monotonic_ns();
            {
                std::lock_guard<std::mutex> lock(guard);
                if(snapshot->current(window,surface_generation,presentation_epoch)) {
                    if(configure) {
                        last_buffer_width=geometry_width;last_buffer_height=geometry_height;last_buffer_format=geometry_format;
                        last_geometry_error=geometry_code;
                        if(geometry_code){++geometry_failures;window_error("geometry",geometry_code);}
                    }
                    if(lock_code==0) {
                        last_buffer_format=target.format;last_buffer_width=target.width;last_buffer_height=target.height;last_buffer_stride=target.stride;
                        if(!valid_buffer) {
                            ++invalid_buffers;++window_failures;window_error("buffer layout",-1);sequence=0;
                            if(post_code){last_post_error=post_code;if(!first_post_error)first_post_error=post_code;window_error("post invalid buffer",post_code);}
                        } else if(post_code==0) {
                            ++presented_frames;
                            if(!first_present_ns)first_present_ns=now;
                            if(previous_present_ns) {
                                const auto gap=now-previous_present_ns;max_present_gap_ns=std::max(max_present_gap_ns,gap);
                                const uint64_t limits[]={16666667,33333334,50000000,100000000,250000000};
                                size_t bucket=0;while(bucket<5 && gap>limits[bucket])++bucket;++present_intervals[bucket];
                            }
                            last_present_ns=previous_present_ns=now;max_present_work_ns=std::max(max_present_work_ns,now-begin);
                        } else {++window_failures;sequence=0;last_post_error=post_code;if(!first_post_error)first_post_error=post_code;window_error("post",post_code);}
                    } else {++window_failures;sequence=0;last_lock_error=lock_code;if(!first_lock_error)first_lock_error=lock_code;window_error("lock",lock_code);}
                }
            }
        }
        snapshot.reset(); // Platform release is outside guard, including detach.
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
        auto row=env->NewObjectArray(11,str,nullptr);
        lt_board_info facts=LT_INIT(lt_board_info);
        if(status==LT_OK)status=lt_engine_board("libqemu-arm.so",record.board,&facts,&error);
        std::vector<std::string> values=status==LT_OK?std::vector<std::string>{record.id,record.display_name,record.board,record.build,std::to_string(record.selected),std::to_string(record.dirty),std::to_string(facts.max_contacts),(facts.capabilities&2)?"1":"0",(facts.capabilities&1)?"1":"0",record.bootrom_path[0] && access(record.bootrom_path,R_OK)==0?"1":"0",record.guest_package_path[0] && access(record.guest_package_path,R_OK)==0?"1":"0"}:std::vector<std::string>{"",result(status,error)};
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
static jstring start_runtime(JNIEnv *env,jstring root,jstring id,bool allow_dirty) {
    bool qmp_requested=false;
    bool touch_trace_requested=false;
    {
        std::lock_guard<std::mutex> lock(guard);
        if(runtime || starting)return string(env,"Worker already consumed; stop and start a fresh worker");
        starting=true;diagnostic.clear();qmp_requested=debug_qmp_armed;debug_qmp_armed=false;
        touch_trace_requested=debug_touch_trace_armed;debug_touch_trace_closed=true;
        graphics_provider_fixed=true;
        if(!graphics_provider_failure.empty()) {
            starting=false;diagnostic=graphics_provider_failure;return string(env,diagnostic);
        }
        if(setenv("LIGHTTOUCH_GLES_PROVIDER",graphics_provider_name(),1)!=0) {
            starting=false;diagnostic="Cannot select the fixed guest graphics provider";return string(env,diagnostic);
        }
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
    if(status==LT_OK)status=lt_library_device_open(library,record.value,allow_dirty?1u:0u,&prepared_device,&error);
    if(status==LT_OK) {
        const std::string path=std::string(selected.state_path)+"/engine-"+std::to_string(getpid())+".log";
        const int log_fd=open(path.c_str(),O_CREAT|O_WRONLY|O_APPEND|O_CLOEXEC,0600);
        if(log_fd<0 || fchmod(log_fd,0600)!=0 || dup2(log_fd,STDOUT_FILENO)<0 || dup2(log_fd,STDERR_FILENO)<0) {
            status=LT_IO;error.code=LT_IO;std::snprintf(error.message,sizeof(error.message),"Cannot retain private guest diagnostics; check available storage");
        } else {
            std::setvbuf(stdout,nullptr,_IOLBF,0);std::setvbuf(stderr,nullptr,_IONBF,0);
            std::lock_guard<std::mutex> lock(guard);engine_log_path=path;
        }
        if(log_fd>=0)close(log_fd);
    }
    if(status==LT_OK)status=lt_engine_board("libqemu-arm.so",selected.board,&prepared_board,&error);
    std::string serial=std::string(selected.state_path)+"/guest-serial.log";
    lt_runtime_config engine=LT_INIT(lt_runtime_config);
    engine.engine_library="libqemu-arm.so";engine.device=prepared_device;engine.boot=lt_boot_config LT_INIT(lt_boot_config);
    engine.boot.machine=nullptr;engine.boot.bootrom_path=selected.bootrom_path[0]?selected.bootrom_path:nullptr;
    engine.boot.guest_package_path=selected.guest_package_path;engine.boot.serial_path=serial.c_str();engine.boot.audio_enabled=1;
    if(status==LT_OK)status=lt_runtime_create(&engine,&prepared_runtime,&error);
#ifdef LT_ANDROID_DEBUG
    if(status==LT_OK && touch_trace_requested) {
        status=lt_runtime_debug_touch_trace_enable(prepared_runtime,&error);
        if(status==LT_OK){std::lock_guard<std::mutex> lock(guard);debug_touch_trace_configured=true;}
    }
#else
    (void)touch_trace_requested;
#endif
    if(status==LT_OK && qmp_requested) {
        status=lt_runtime_debug_qmp_enable(prepared_runtime,&error);
        char endpoint[1024]={};
        if(status==LT_OK)status=lt_runtime_debug_qmp_path(prepared_runtime,endpoint,sizeof(endpoint),&error);
        if(status==LT_OK){std::lock_guard<std::mutex> lock(guard);debug_qmp_path=endpoint;}
    }
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
    if(startup_pause_requested && !startup_stop_requested) {
        lt_error pause_error=LT_INIT(lt_error);
        auto paused=lt_runtime_pause(runtime,1,&pause_error);
        if(paused!=LT_OK)diagnostic=result(paused,pause_error);
    }
    if(startup_stop_requested) {
        lt_error stop_error=LT_INIT(lt_error);
        auto stopped=lt_runtime_request_shutdown(runtime,&stop_error);
        if(stopped!=LT_OK)diagnostic=result(stopped,stop_error);
    }
    if(!presenter_started){presenter_started=true;std::thread(present).detach();}
    return string(env,diagnostic);
}

JNI(start) jstring JNICALL Java_org_lighttouch_android_Native_start(JNIEnv *env,jobject,jstring root,jstring id) {
    // Ordinary startup must not silently admit an interrupted writable state.
    return start_runtime(env,root,id,false);
}
JNI(recoverStart) jstring JNICALL Java_org_lighttouch_android_Native_recoverStart(JNIEnv *env,jobject,jstring root,jstring id) {
    return start_runtime(env,root,id,true);
}

JNI(status) jobjectArray JNICALL Java_org_lighttouch_android_Native_status(JNIEnv *env,jobject) {
    std::lock_guard<std::mutex> lock(guard);
    const char *states[]={"stopped","starting","running","paused","stopping","failed"};
    lt_runtime_status s=LT_INIT(lt_runtime_status);lt_error poll_error=LT_INIT(lt_error);
    // Poll errors belong to this operation; observations uses its own lt_error.
    // A later successful native call must never clear the actionable poll message.
    std::string detail=diagnostic;
    if(runtime) { auto status=lt_runtime_poll(runtime,&s,&poll_error);if(status!=LT_OK) { diagnostic=result(status,poll_error);detail=diagnostic; }
        else {
            if(s.state==LT_STOPPED && (s.dirty || !s.guest_shutdown_confirmed))s.state=LT_FAILED;
            if(s.state==LT_FAILED && diagnostic.empty())diagnostic="Guest exited without a confirmed clean shutdown (code "+std::to_string(s.engine_exit)+"); writable state retained. Stop this worker, then explicitly recover in a fresh worker. Diagnostics are retained.";
            detail="Gyro deferred/unqualified "+std::string((board.capabilities & 2)?"(modeled)":"(not modeled)")+"; contacts "+std::to_string(board.max_contacts)+"; frames "+std::to_string(s.frames)+"; "+(s.dirty?"writable state dirty":"writable state clean")+"; "+(s.guest_shutdown_confirmed?"guest shutdown confirmed":"shutdown unconfirmed");
        }
    }
    auto arr=env->NewObjectArray(8,env->FindClass("java/lang/String"),nullptr);
    env->SetObjectArrayElement(arr,0,string(env,states[starting ? (startup_stop_requested ? 4u : 1u) : (!diagnostic.empty() && !runtime ? 5u : std::min(s.state,5u))]));
    env->SetObjectArrayElement(arr,1,string(env,detail));
    env->SetObjectArrayElement(arr,2,string(env,diagnostic));env->SetObjectArrayElement(arr,3,string(env,engine_log_path));env->SetObjectArrayElement(arr,4,string(env,debug_qmp_path));
    env->SetObjectArrayElement(arr,5,string(env,graphics_provider_name()));
    env->SetObjectArrayElement(arr,6,string(env,graphics_provider_fixed?"1":"0"));
    env->SetObjectArrayElement(arr,7,string(env,debug_touch_trace_configured?"1":"0"));return arr;
}
JNI(surface) void JNICALL Java_org_lighttouch_android_Native_surface(JNIEnv *env,jobject,jobject surface) {
    uint64_t request;
    {
        std::lock_guard<std::mutex> lock(guard);
        request=++surface_request;
    }
    // Conversion may enter platform code. A later clear/attach wins even if
    // this earlier conversion takes longer to return.
    ANativeWindow *incoming=surface?ANativeWindow_fromSurface(env,surface):nullptr;
    ANativeWindow *retired=nullptr;
    {
        std::lock_guard<std::mutex> lock(guard);
        if(request==surface_request) {
            retired=window;window=incoming;incoming=nullptr;
            ++surface_generation;++presentation_epoch;previous_present_ns=0;
            if(window)sequence=0; // Replay the latest cache on a replacement.
        }
    }
    if(retired)ANativeWindow_release(retired);
    if(incoming)ANativeWindow_release(incoming);
}
JNI(pause) jstring JNICALL Java_org_lighttouch_android_Native_pause(JNIEnv *env,jobject,jboolean paused) {
    std::lock_guard<std::mutex> lock(guard);lt_error error=LT_INIT(lt_error);
    // An old in-flight failure cannot rewind the new epoch's cursor. Replay
    // the cache here so Resume does not depend on a newer guest repaint.
    ++presentation_epoch;previous_present_ns=0;sequence=0;
    if(starting){startup_pause_requested=paused;return string(env,"");}
    return string(env,runtime?result(lt_runtime_pause(runtime,paused,&error),error):"No active session");
}
JNI(stop) jstring JNICALL Java_org_lighttouch_android_Native_stop(JNIEnv *env,jobject) {
    std::lock_guard<std::mutex> lock(guard);lt_error error=LT_INIT(lt_error);
    ++presentation_epoch;previous_present_ns=0;
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
    if(!runtime) return string(env,"No active guest session; touch was not admitted");
    if(!frame.width || !frame.height) {
        lt_error cancelled=LT_INIT(lt_error);
        const auto cancellation=lt_runtime_cancel_input(runtime,&cancelled);
        std::string failure="No active guest frame; gesture cancelled";
        if(cancellation!=LT_OK)failure += "; input release failed: "+result(cancellation,cancelled);
        return string(env,failure);
    }
    jint id[LT_MAX_CONTACTS], phase[LT_MAX_CONTACTS];jfloat coords[LT_MAX_CONTACTS*2];
    env->GetIntArrayRegion(ids,0,count,id);env->GetIntArrayRegion(phases,0,count,phase);env->GetFloatArrayRegion(xy,0,count*2,coords);
    lt_touch_batch batch=LT_INIT(lt_touch_batch);batch.timestamp_ns=time;batch.count=count;
    const auto view=viewport(width,height);lt_error error=LT_INIT(lt_error);
    for(jsize i=0;i<count;++i) {
        batch.contacts[i]=lt_contact LT_INIT(lt_contact);batch.contacts[i].id=id[i];batch.contacts[i].phase=phase[i];
        auto status=lt_map_contact(coords[i*2],coords[i*2+1],view.left,view.top,view.width,view.height,0,&batch.contacts[i].x,&batch.contacts[i].y);
        if(status!=LT_OK) {
            const auto cancellation=lt_runtime_cancel_input(runtime,&error);
            std::string failure="Touch left guest viewport; gesture cancelled";
            if(cancellation!=LT_OK)failure += "; input release failed: "+result(cancellation,error);
            return string(env,failure);
        }
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
    if(status==LT_AGAIN)return 0;
    if(status!=LT_OK){++pcm_failures;return -jint(status);}
    if(pcm.channels>2||pcm.channels<1||pcm.frames*pcm.channels>data.size()){++pcm_failures;return -jint(LT_INVALID);}
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

JNI(observations) jlongArray JNICALL Java_org_lighttouch_android_Native_observations(JNIEnv *env,jobject) {
    std::lock_guard<std::mutex> lock(guard);
    lt_runtime_io_stats shared=LT_INIT(lt_runtime_io_stats);lt_error error=LT_INIT(lt_error);
    if(runtime)lt_runtime_io_statistics(runtime,&shared,&error);
    lt_runtime_diagnostics observer=LT_INIT(lt_runtime_diagnostics);
    lt_error observer_error=LT_INIT(lt_error),agent_error=LT_INIT(lt_error);
    const auto observer_status=runtime?lt_runtime_diagnose(runtime,&observer,&observer_error):LT_AGAIN;
    uint32_t live_agent=0;
    const auto agent_status=runtime?lt_runtime_guest_status(runtime,&live_agent,&agent_error):LT_AGAIN;
    // These samples use separate error buffers and never replace poll/control
    // failures. No heartbeat age or socket endpoint exists in this ABI.
    jlong values[]={jlong(monotonic_ns()),jlong(presented_frames),jlong(first_present_ns),jlong(last_present_ns),
        jlong(max_present_gap_ns),jlong(max_present_work_ns),jlong(window_failures),jlong(frame_copy_failures),
        jlong(surface_generation),jlong(frame.sequence),jlong(frame.timestamp_ns),jlong(pcm_failures),jlong(pcm.dropped_frames),
        jlong(present_intervals[0]),jlong(present_intervals[1]),jlong(present_intervals[2]),jlong(present_intervals[3]),jlong(present_intervals[4]),jlong(present_intervals[5]),jlong(shared.frame_count),jlong(shared.frame_sequence),jlong(shared.frame_timestamp_ns),
        jlong(shared.frame_published_monotonic_ns),jlong(shared.pcm_captured_frames),jlong(shared.pcm_pulled_frames),jlong(shared.pcm_dropped_frames),
        jlong(shared.input_requests),jlong(shared.pcm_queued_packets),first_lock_error,last_lock_error,first_post_error,last_post_error,last_geometry_error,
        jlong(geometry_failures),last_buffer_format,last_buffer_width,last_buffer_height,last_buffer_stride,jlong(invalid_buffers),jlong(first_window_error_ns),jlong(observer_status),jlong(observer.engine_ready),
        jlong(observer.display_sleeping),jlong(observer.backlight_level),jlong(agent_status),jlong(live_agent),
        jlong(observer.shutdown_phase),jlong(observer.shutdown_route),jlong(observer.shutdown_reply_status),
        jlong(observer.shutdown_reply_received),jlong(frame.width),jlong(frame.height),
        jlong(frame_sample_pixels),jlong(frame_nonzero_rgb_samples),jlong(frame_rgb_sample_sum)};
    auto array=env->NewLongArray(sizeof(values)/sizeof(values[0]));
    env->SetLongArrayRegion(array,0,sizeof(values)/sizeof(values[0]),values);return array;
}

JNI(shutdownGraceMillis) jint JNICALL Java_org_lighttouch_android_Native_shutdownGraceMillis(JNIEnv *,jobject) {
    return jint(LT_SHUTDOWN_GRACE_MS);
}


JNI(debugSelectGraphicsProvider) jstring JNICALL Java_org_lighttouch_android_Native_debugSelectGraphicsProvider(JNIEnv *env,jobject,jint choice) {
#ifndef LT_ANDROID_DEBUG
    return string(env,"Graphics provider diagnosis is unavailable in this build");
#else
    std::lock_guard<std::mutex> lock(guard);
    if(choice!=0 && choice!=1)return string(env,"Unsupported fixed graphics provider");
    if(runtime || starting)return string(env,"Graphics provider selection requires a fresh unused worker");
    if(graphics_provider_fixed)return string(env,!graphics_provider_failure.empty()?graphics_provider_failure:
        unsigned(choice)==graphics_provider_choice?"":"Graphics provider is fixed; use a fresh worker to change it");
#ifndef __x86_64__
    if(choice==1)return string(env,"The packaged ANGLE experiment is available only for x86-64");
#endif
    graphics_provider_choice=unsigned(choice);graphics_provider_fixed=true;
    if(choice==1) {
        // Fixed public SONAMEs resolve inside this app's linker namespace. No
        // caller path, environment variable name or general library loader.
        const char *names[]={"libGLESv2_lighttouch_angle.so","libEGL_lighttouch_angle.so","libGLESv1_CM_lighttouch_angle.so"};
        for(unsigned i=0;i<3;++i) {
            angle_provider_handles[i]=dlopen(names[i],RTLD_NOW|RTLD_LOCAL);
            if(!angle_provider_handles[i]) {
                const char *reason=dlerror();
                graphics_provider_failure="Packaged ANGLE provider is unavailable: "+std::string(reason?reason:"library load failed").substr(0,512)+"; start a fresh worker after correcting the package";
                return string(env,graphics_provider_failure);
            }
        }
        if(setenv("LT_GLES_DRIVER_DIAGNOSTIC","1",1)!=0) {
            graphics_provider_failure="Cannot retain graphics provider diagnostics; use a fresh worker";return string(env,graphics_provider_failure);
        }
    }
    return string(env,"");
#endif
}

JNI(debugEnableTouchTrace) jstring JNICALL Java_org_lighttouch_android_Native_debugEnableTouchTrace(JNIEnv *env,jobject) {
#ifdef LT_ANDROID_DEBUG
    std::lock_guard<std::mutex> lock(guard);
    if(runtime || starting || debug_touch_trace_closed)return string(env,"Touch trace selection requires a fresh unused worker");
    debug_touch_trace_armed=true;return string(env,"");
#else
    return string(env,"Debug diagnosis is unavailable in this build");
#endif
}
JNI(debugArmQmp) jstring JNICALL Java_org_lighttouch_android_Native_debugArmQmp(JNIEnv *env,jobject) {
#ifdef LT_ANDROID_DEBUG
    std::lock_guard<std::mutex> lock(guard);
    if(runtime || starting)return string(env,"Debug diagnosis requires a fresh unused worker");
    // Fixed copied-buffer metrics only: no caller environment name or value,
    // and no change to default workers or release builds.
    if(setenv("IT_GLES_FRAME_METRICS","1",1)!=0)
        return string(env,"Cannot enable copied-frame diagnosis: "+std::string(std::strerror(errno)).substr(0,256));
    debug_qmp_armed=true;return string(env,"");
#else
    return string(env,"Debug diagnosis is unavailable in this build");
#endif
}
JNI(debugQmpSnapshot) jobjectArray JNICALL Java_org_lighttouch_android_Native_debugQmpSnapshot(JNIEnv *env,jobject) {
    std::string failure,path;
#ifdef LT_ANDROID_DEBUG
    std::string endpoint;
    {
        std::lock_guard<std::mutex> lock(guard);endpoint=debug_qmp_path;
        if(!endpoint.empty() && !engine_log_path.empty())path=engine_log_path.substr(0,engine_log_path.find_last_of('/'))+
            "/qmp-snapshot-"+std::to_string(getpid())+"-"+std::to_string(monotonic_ns())+".jsonl";
    }
    if(endpoint.empty() || path.empty())failure="Debug endpoint was not enabled before this session";
    else {
        const int fd=open(path.c_str(),O_CREAT|O_EXCL|O_WRONLY|O_CLOEXEC,0600);
        if(fd<0)failure="Cannot retain private debug snapshot";
        else {
            // No session/Surface mutex is held across the bounded socket calls.
            const int result=lighttouch_debug_qmp_snapshot(endpoint.c_str(),fd);
            const int saved=errno;close(fd);
            if(result)failure="Read-only debug snapshot failed: "+std::string(std::strerror(saved));
        }
    }
#else
    failure="Debug diagnosis is unavailable in this build";
#endif
    auto result=env->NewObjectArray(2,env->FindClass("java/lang/String"),nullptr);
    env->SetObjectArrayElement(result,0,string(env,failure));env->SetObjectArrayElement(result,1,string(env,path));return result;
}

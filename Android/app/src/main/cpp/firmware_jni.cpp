#include "lighttouch_firmware.h"
#include <jni.h>
#include <atomic>
#include <mutex>
#include <string>
std::mutex fw_guard;
std::atomic<bool> fw_cancel{false},fw_consumed{false};
std::string fw_state="idle",fw_stage,fw_message;
uint64_t fw_done=0,fw_total=0;
int fw_code=0;
struct FwUtf {
    JNIEnv *env;jstring source;const char *value;
    FwUtf(JNIEnv *e,jstring s):env(e),source(s),value(e->GetStringUTFChars(s,nullptr)){}
    ~FwUtf(){if(value)env->ReleaseStringUTFChars(source,value);}
};
static int fw_progress(void *,const char *stage,uint64_t done,uint64_t total) {
    std::lock_guard<std::mutex> lock(fw_guard);fw_stage=stage?stage:"";fw_done=done;fw_total=total;
    return fw_cancel.load()?1:0;
}
extern "C" JNIEXPORT jboolean JNICALL Java_org_lighttouch_android_FirmwareNative_supports(JNIEnv *env,jobject,jstring id) {
    FwUtf name(env,id);return lt_firmware_supports(name.value)?JNI_TRUE:JNI_FALSE;
}
extern "C" JNIEXPORT jint JNICALL Java_org_lighttouch_android_FirmwareNative_prepare(JNIEnv *env,jobject,jstring catalog,jstring id,jstring ipsw,jstring tools,jstring inventory,jstring destination) {
    if(fw_consumed.exchange(true))return LT_FW_BUSY;
    FwUtf c(env,catalog),i(env,id),p(env,ipsw),t(env,tools),m(env,inventory),d(env,destination);
    { std::lock_guard<std::mutex> lock(fw_guard);fw_state="preparing"; }
    lt_fw_request request{};request.size=sizeof(request);request.version=LT_FIRMWARE_ABI;
    request.catalog_path=c.value;request.firmware_id=i.value;request.ipsw_path=p.value;
    request.guest_tools_path=t.value;request.guest_inventory_path=m.value;request.destination=d.value;request.progress=fw_progress;
    lt_fw_error error{};error.size=sizeof(error);error.version=LT_FIRMWARE_ABI;
    auto result=lt_firmware_prepare(&request,&error);
    { std::lock_guard<std::mutex> lock(fw_guard);fw_code=result;fw_message=error.message;
        fw_state=result==LT_FW_OK?"complete":result==LT_FW_CANCELLED?"cancelled":"failed"; }
    return result;
}
extern "C" JNIEXPORT jobjectArray JNICALL Java_org_lighttouch_android_FirmwareNative_status(JNIEnv *env,jobject) {
    std::lock_guard<std::mutex> lock(fw_guard);
    auto values=env->NewObjectArray(7,env->FindClass("java/lang/String"),nullptr);
    std::string row[]={fw_state,fw_stage,std::to_string(fw_done),std::to_string(fw_total),std::to_string(fw_code),fw_message,fw_consumed.load()?"1":"0"};
    for(int i=0;i<7;++i) { auto text=env->NewStringUTF(row[i].c_str());env->SetObjectArrayElement(values,i,text);env->DeleteLocalRef(text); }
    return values;
}
extern "C" JNIEXPORT void JNICALL Java_org_lighttouch_android_FirmwareNative_cancel(JNIEnv *,jobject) { fw_cancel=true; }

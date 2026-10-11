// Runs the YOLO model with ncnn (Vulkan on the GPU). Only inference lives
// here; decoding detections and outlines is in Kotlin (Detector.kt).

#include <android/asset_manager_jni.h>
#include <android/bitmap.h>
#include <android/log.h>
#include <jni.h>

#include <mutex>

#include "net.h"
#include "gpu.h"

#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, "Hurmat", __VA_ARGS__)

// One loaded model. Handles are passed to Java as jlong.
struct Model {
    ncnn::Net net;
    ncnn::Extractor *ex = nullptr;  // last run, kept for the lazy protos() call
    std::mutex lock;
    ~Model() { delete ex; }
};

static const float PAD_VALUE = 114.f;

extern "C" JNIEXPORT jlong JNICALL
Java_org_hurmat_tasaaweer_NativeDetector_load(JNIEnv *env, jobject, jobject assets,
                                              jstring param, jstring model, jboolean useGpu,
                                              jint threads) {
    AAssetManager *mgr = AAssetManager_fromJava(env, assets);
    auto *m = new Model();
    bool gpu = useGpu && ncnn::get_gpu_count() > 0;
    m->net.opt.use_vulkan_compute = gpu;
    m->net.opt.use_fp16_storage = true;
    m->net.opt.use_fp16_arithmetic = true;
    m->net.opt.use_fp16_packed = true;
    m->net.opt.num_threads = threads;

    const char *p = env->GetStringUTFChars(param, nullptr);
    const char *b = env->GetStringUTFChars(model, nullptr);
    int r1 = m->net.load_param(mgr, p);
    int r2 = m->net.load_model(mgr, b);
    LOGI("model %s loaded (%s), gpu=%d", p, (r1 == 0 && r2 == 0) ? "ok" : "FAILED", gpu);
    env->ReleaseStringUTFChars(param, p);
    env->ReleaseStringUTFChars(model, b);
    if (r1 != 0 || r2 != 0) {
        delete m;
        return 0;
    }
    return (jlong) m;
}

extern "C" JNIEXPORT void JNICALL
Java_org_hurmat_tasaaweer_NativeDetector_free(JNIEnv *, jobject, jlong handle) {
    delete (Model *) handle;
}

extern "C" JNIEXPORT jboolean JNICALL
Java_org_hurmat_tasaaweer_NativeDetector_usesGpu(JNIEnv *, jobject, jlong handle) {
    return ((Model *) handle)->net.opt.use_vulkan_compute;
}

// Letterbox the bitmap region (x, y, w, h) to imgsz x imgsz and run the model.
// Writes scale, left, top into `meta` and returns out0 as a flat
// (rows x anchors) float array; `shape` receives {rows, anchors}.
extern "C" JNIEXPORT jfloatArray JNICALL
Java_org_hurmat_tasaaweer_NativeDetector_run(JNIEnv *env, jobject, jlong handle, jobject bitmap,
                                             jint x, jint y, jint w, jint h, jint imgsz,
                                             jfloatArray meta, jintArray shape) {
    auto *m = (Model *) handle;
    std::lock_guard<std::mutex> lock(m->lock);

    float scale = (float) imgsz / (float) std::max(w, h);
    int tw = std::max(1, (int) (w * scale + 0.5f));
    int th = std::max(1, (int) (h * scale + 0.5f));
    ncnn::Mat in = ncnn::Mat::from_android_bitmap_roi_resize(env, bitmap, ncnn::Mat::PIXEL_RGB,
                                                             x, y, w, h, tw, th);
    int padw = imgsz - tw, padh = imgsz - th;
    int left = padw / 2, top = padh / 2;
    ncnn::Mat padded;
    ncnn::copy_make_border(in, padded, top, padh - top, left, padw - left,
                           ncnn::BORDER_CONSTANT, PAD_VALUE);
    const float norm[3] = {1 / 255.f, 1 / 255.f, 1 / 255.f};
    padded.substract_mean_normalize(nullptr, norm);

    delete m->ex;
    m->ex = new ncnn::Extractor(m->net.create_extractor());
    m->ex->input("in0", padded);
    ncnn::Mat out;
    m->ex->extract("out0", out);  // (rows, anchors) as h x w

    float mm[3] = {scale, (float) left, (float) top};
    env->SetFloatArrayRegion(meta, 0, 3, mm);
    jint s[2] = {out.h, out.w};
    env->SetIntArrayRegion(shape, 0, 2, s);

    jfloatArray result = env->NewFloatArray(out.h * out.w);
    for (int r = 0; r < out.h; r++)
        env->SetFloatArrayRegion(result, r * out.w, out.w, out.row(r));
    return result;
}

// Mask prototypes of the last run (segmentation models), as a flat
// (channels x h x w) array; only computed when asked for. `shape` receives
// {channels, h, w}.
extern "C" JNIEXPORT jfloatArray JNICALL
Java_org_hurmat_tasaaweer_NativeDetector_protos(JNIEnv *env, jobject, jlong handle, jintArray shape) {
    auto *m = (Model *) handle;
    std::lock_guard<std::mutex> lock(m->lock);
    if (!m->ex) return nullptr;
    ncnn::Mat p;
    if (m->ex->extract("out1", p) != 0) return nullptr;
    jint s[3] = {p.c, p.h, p.w};
    env->SetIntArrayRegion(shape, 0, 3, s);
    int plane = p.h * p.w;
    jfloatArray result = env->NewFloatArray(p.c * plane);
    for (int c = 0; c < p.c; c++)
        env->SetFloatArrayRegion(result, c * plane, plane, p.channel(c));
    return result;
}

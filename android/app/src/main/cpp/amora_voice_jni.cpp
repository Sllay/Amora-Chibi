#include <jni.h>
#include <android/log.h>

#include <cstddef>
#include <vector>

#include "amora_voice_processor.h"

namespace {
constexpr char kTag[] = "AmoraVoiceNative";
}

extern "C" JNIEXPORT jfloatArray JNICALL
Java_dev_amora_chibi_voice_AmoraVoiceNative_process(
    JNIEnv* env,
    jobject,
    jfloatArray samples,
    jint sampleRate,
    jfloat pitchRangeScale,
    jfloat f0RegisterScale,
    jfloat energyScale,
    jfloat softness,
    jfloat firmness) {
  if (samples == nullptr || sampleRate <= 0) return env->NewFloatArray(0);
  const jsize length = env->GetArrayLength(samples);
  if (length <= 0) return env->NewFloatArray(0);

  std::vector<jfloat> input(static_cast<std::size_t>(length));
  env->GetFloatArrayRegion(samples, 0, length, input.data());

  try {
    const auto output = amora_voice::Process(
        input.data(),
        static_cast<std::size_t>(length),
        sampleRate,
        pitchRangeScale,
        f0RegisterScale,
        energyScale,
        softness,
        firmness);
    if (output.size() != static_cast<std::size_t>(length)) return env->NewFloatArray(0);

    jfloatArray result = env->NewFloatArray(length);
    if (result != nullptr) {
      env->SetFloatArrayRegion(result, 0, length, output.data());
    }
    return result;
  } catch (...) {
    __android_log_print(ANDROID_LOG_ERROR, kTag, "WORLD processing threw an exception");
    return env->NewFloatArray(0);
  }
}

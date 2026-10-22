#pragma once

#include <cstddef>
#include <vector>

namespace amora_voice {

std::vector<float> Process(
    const float* samples,
    std::size_t length,
    int sample_rate,
    float pitch_range_scale,
    float f0_register_scale,
    float energy_scale,
    float softness,
    float firmness);

}  // namespace amora_voice

extern "C" int amora_voice_process_f32(
    const float* input,
    std::size_t length,
    int sample_rate,
    float pitch_range_scale,
    float f0_register_scale,
    float energy_scale,
    float softness,
    float firmness,
    float* output,
    std::size_t output_capacity);

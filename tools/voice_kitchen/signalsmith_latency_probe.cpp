#include <cstdlib>
#include <iomanip>
#include <iostream>

#include "signalsmith-stretch/signalsmith-stretch.h"

using Stretch = signalsmith::stretch::SignalsmithStretch<float>;

int main(int argc, char **argv) {
    const int sampleRate = argc > 1 ? std::atoi(argv[1]) : 24000;
    const double blockMs = argc > 2 ? std::atof(argv[2]) : 120.0;
    const double overlap = argc > 3 ? std::atof(argv[3]) : 4.0;

    if (sampleRate <= 0 || blockMs <= 0.0 || overlap <= 0.0) {
        std::cerr << "invalid arguments\n";
        return 2;
    }

    const int blockSamples = static_cast<int>(sampleRate * blockMs * 0.001);
    const int intervalSamples = static_cast<int>(blockSamples / overlap);
    if (blockSamples <= 0 || intervalSamples <= 0) {
        std::cerr << "invalid block/interval\n";
        return 2;
    }

    Stretch stretch;
    stretch.configure(1, blockSamples, intervalSamples, false);

    const int inputLatency = stretch.inputLatency();
    const int outputLatency = stretch.outputLatency();
    const int totalLatency = inputLatency + outputLatency;
    const double totalMs = 1000.0 * totalLatency / sampleRate;

    std::cout << std::fixed << std::setprecision(6)
              << "{\n"
              << "  \"sample_rate_hz\": " << sampleRate << ",\n"
              << "  \"block_ms\": " << blockMs << ",\n"
              << "  \"overlap\": " << overlap << ",\n"
              << "  \"block_samples\": " << blockSamples << ",\n"
              << "  \"interval_samples\": " << intervalSamples << ",\n"
              << "  \"input_latency_samples\": " << inputLatency << ",\n"
              << "  \"output_latency_samples\": " << outputLatency << ",\n"
              << "  \"total_algorithmic_latency_samples\": " << totalLatency << ",\n"
              << "  \"total_algorithmic_latency_ms\": " << totalMs << "\n"
              << "}\n";
    return 0;
}

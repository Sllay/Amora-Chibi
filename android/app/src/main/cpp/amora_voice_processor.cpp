#include "amora_voice_processor.h"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <numeric>
#include <stdexcept>
#include <vector>

#include "world/cheaptrick.h"
#include "world/d4c.h"
#include "world/dio.h"
#include "world/stonemask.h"
#include "world/synthesis.h"

namespace {
constexpr double kFramePeriodMs = 5.0;
constexpr double kTargetF0Hz = 284.8;
constexpr double kMaxF0Ratio = 1.186;
constexpr double kBasePitchRange = 1.048;
constexpr double kAperiodicityScale = 0.965;
constexpr double kHighShelfDb = -2.36;
constexpr double kHarmonicDrive = 1.30;
constexpr double kHarmonicWet = 0.05;

struct Anchor { double hz; double ratio; };
constexpr Anchor kWarp[] = {
    {0.0, 1.0}, {250.0, 1.0}, {700.0, 1.126}, {1500.0, 1.111},
    {3000.0, 1.068}, {5000.0, 1.020}, {8000.0, 1.0},
};

double clampd(double value, double low, double high) {
  return std::max(low, std::min(high, value));
}

double median(std::vector<double> values) {
  if (values.empty()) return 0.0;
  const std::size_t middle = values.size() / 2;
  std::nth_element(values.begin(), values.begin() + middle, values.end());
  double result = values[middle];
  if (values.size() % 2 == 0) {
    const auto lower = std::max_element(values.begin(), values.begin() + middle);
    result = (*lower + result) * 0.5;
  }
  return result;
}

double interp(const std::vector<double>& x, const std::vector<double>& y, double q) {
  if (q <= x.front()) return y.front();
  if (q >= x.back()) return y.back();
  auto upper = std::upper_bound(x.begin(), x.end(), q);
  std::size_t hi = static_cast<std::size_t>(upper - x.begin());
  std::size_t lo = hi - 1;
  const double span = x[hi] - x[lo];
  const double t = span > 0.0 ? (q - x[lo]) / span : 0.0;
  return y[lo] + (y[hi] - y[lo]) * t;
}

void applyF0(std::vector<double>& f0, double registerScale, double expressionRange) {
  std::vector<double> voiced;
  voiced.reserve(f0.size());
  for (double value : f0) if (value > 0.0) voiced.push_back(value);
  const double sourceMedian = median(voiced);
  if (sourceMedian <= 0.0) return;

  const double target = kTargetF0Hz * clampd(registerScale, 0.97, 1.02);
  const double desiredRatio = target / sourceMedian;
  const double appliedRatio = clampd(desiredRatio, 1.0, kMaxF0Ratio);
  const double targetMedian = sourceMedian * appliedRatio;

  std::vector<double> logs;
  logs.reserve(voiced.size());
  for (double value : voiced) logs.push_back(std::log(value));
  const double logMedian = median(logs);
  const double targetLogMedian = std::log(targetMedian);
  const double range = kBasePitchRange * clampd(expressionRange, 0.90, 1.08);
  for (double& value : f0) {
    if (value > 0.0) value = std::exp(targetLogMedian + (std::log(value) - logMedian) * range);
  }
}

std::vector<double> buildSourceMap(int sampleRate, int bins) {
  const double nyquist = sampleRate * 0.5;
  std::vector<double> sourceHz;
  std::vector<double> targetHz;
  for (const auto& anchor : kWarp) {
    if (anchor.hz >= nyquist) break;
    sourceHz.push_back(anchor.hz);
    targetHz.push_back(anchor.hz * anchor.ratio);
  }
  if (sourceHz.empty() || sourceHz.front() != 0.0) {
    sourceHz.insert(sourceHz.begin(), 0.0);
    targetHz.insert(targetHz.begin(), 0.0);
  }
  if (sourceHz.back() < nyquist) {
    sourceHz.push_back(nyquist);
    targetHz.push_back(nyquist);
  } else {
    sourceHz.back() = nyquist;
    targetHz.back() = nyquist;
  }

  std::vector<double> map(static_cast<std::size_t>(bins));
  for (int bin = 0; bin < bins; ++bin) {
    const double outputHz = nyquist * bin / std::max(1, bins - 1);
    map[static_cast<std::size_t>(bin)] = interp(targetHz, sourceHz, outputHz);
  }
  return map;
}

void warpSpectrum(std::vector<std::vector<double>>& sp, int sampleRate, double softness, double firmness) {
  if (sp.empty() || sp.front().empty()) return;
  const int bins = static_cast<int>(sp.front().size());
  const double nyquist = sampleRate * 0.5;
  const auto sourceMap = buildSourceMap(sampleRate, bins);
  const double toneDb = clampd(0.90 * (firmness - 0.15) - 1.10 * (softness - 0.25), -0.70, 0.80);

  for (auto& frame : sp) {
    const std::vector<double> original = frame;
    const double sourceEnergy = std::accumulate(original.begin(), original.end(), 0.0);
    for (int bin = 0; bin < bins; ++bin) {
      const double sourcePos = sourceMap[static_cast<std::size_t>(bin)] / nyquist * (bins - 1);
      const int lo = std::max(0, std::min(bins - 1, static_cast<int>(std::floor(sourcePos))));
      const int hi = std::min(bins - 1, lo + 1);
      const double t = sourcePos - lo;
      frame[static_cast<std::size_t>(bin)] = original[static_cast<std::size_t>(lo)] * (1.0 - t) + original[static_cast<std::size_t>(hi)] * t;
    }
    const double outputEnergy = std::accumulate(frame.begin(), frame.end(), 0.0);
    if (sourceEnergy > 0.0 && outputEnergy > 0.0) {
      const double scale = sourceEnergy / outputEnergy;
      for (double& value : frame) value *= scale;
    }

    for (int bin = 0; bin < bins; ++bin) {
      const double hz = nyquist * bin / std::max(1, bins - 1);
      double shelfDb = 0.0;
      if (hz >= 7000.0) shelfDb = kHighShelfDb;
      else if (hz >= 3500.0) shelfDb = kHighShelfDb * (0.35 + 0.65 * (hz - 3500.0) / 3500.0);
      else if (hz >= 2500.0) shelfDb = kHighShelfDb * 0.35 * (hz - 2500.0) / 1000.0;

      double toneAmount = 0.0;
      if (hz >= 5000.0) toneAmount = 1.0;
      else if (hz > 1800.0) {
        double t = (hz - 1800.0) / 3200.0;
        t = clampd(t, 0.0, 1.0);
        toneAmount = t * t * (3.0 - 2.0 * t);
      }
      const double totalDb = shelfDb + toneDb * toneAmount;
      frame[static_cast<std::size_t>(bin)] *= std::pow(10.0, totalDb / 10.0);
    }
  }
}

void applySignatureAndEnergy(std::vector<double>& y, double energyScale) {
  if (y.empty()) return;
  double sourceSquare = 0.0;
  for (double value : y) sourceSquare += value * value;
  const double sourceRms = std::sqrt(sourceSquare / y.size());
  const double norm = std::tanh(kHarmonicDrive);
  double shapedSquare = 0.0;
  for (double& value : y) {
    const double shaped = std::tanh(value * kHarmonicDrive) / std::max(norm, 1e-9);
    value = value * (1.0 - kHarmonicWet) + shaped * kHarmonicWet;
    shapedSquare += value * value;
  }
  const double shapedRms = std::sqrt(shapedSquare / y.size());
  if (sourceRms > 1e-9 && shapedRms > 1e-9) {
    const double scale = sourceRms / shapedRms;
    for (double& value : y) value *= scale;
  }
  const double energy = clampd(energyScale, 0.88, 1.06);
  double peak = 0.0;
  for (double& value : y) {
    value *= energy;
    peak = std::max(peak, std::abs(value));
  }
  if (peak > 0.99) {
    const double scale = 0.99 / peak;
    for (double& value : y) value *= scale;
  }
}

std::vector<double*> rowPointers(std::vector<std::vector<double>>& matrix) {
  std::vector<double*> rows;
  rows.reserve(matrix.size());
  for (auto& row : matrix) rows.push_back(row.data());
  return rows;
}
}  // namespace

namespace amora_voice {

std::vector<float> Process(
    const float* samples,
    std::size_t length,
    int sample_rate,
    float pitch_range_scale,
    float f0_register_scale,
    float energy_scale,
    float softness,
    float firmness) {
  if (samples == nullptr || length == 0 || sample_rate <= 0) return {};

  std::vector<double> x(length);
  for (std::size_t i = 0; i < length; ++i) x[i] = static_cast<double>(samples[i]);

  DioOption dioOption{};
  InitializeDioOption(&dioOption);
  dioOption.frame_period = kFramePeriodMs;
  const int f0Length = GetSamplesForDIO(sample_rate, static_cast<int>(length), kFramePeriodMs);
  if (f0Length <= 0) return {};
  std::vector<double> timeAxis(static_cast<std::size_t>(f0Length));
  std::vector<double> rawF0(static_cast<std::size_t>(f0Length));
  std::vector<double> f0(static_cast<std::size_t>(f0Length));
  Dio(x.data(), static_cast<int>(length), sample_rate, &dioOption, timeAxis.data(), rawF0.data());
  StoneMask(x.data(), static_cast<int>(length), sample_rate, timeAxis.data(), rawF0.data(), f0Length, f0.data());

  CheapTrickOption cheapOption{};
  InitializeCheapTrickOption(sample_rate, &cheapOption);
  const int fftSize = GetFFTSizeForCheapTrick(sample_rate, &cheapOption);
  const int bins = fftSize / 2 + 1;
  std::vector<std::vector<double>> sp(static_cast<std::size_t>(f0Length), std::vector<double>(static_cast<std::size_t>(bins)));
  auto spRows = rowPointers(sp);
  CheapTrick(x.data(), static_cast<int>(length), sample_rate, timeAxis.data(), f0.data(), f0Length, &cheapOption, spRows.data());

  D4COption d4cOption{};
  InitializeD4COption(&d4cOption);
  std::vector<std::vector<double>> ap(static_cast<std::size_t>(f0Length), std::vector<double>(static_cast<std::size_t>(bins)));
  auto apRows = rowPointers(ap);
  D4C(x.data(), static_cast<int>(length), sample_rate, timeAxis.data(), f0.data(), f0Length, fftSize, &d4cOption, apRows.data());

  applyF0(f0, f0_register_scale, pitch_range_scale);
  warpSpectrum(sp, sample_rate, clampd(softness, 0.0, 1.0), clampd(firmness, 0.0, 1.0));
  for (auto& frame : ap) {
    for (double& value : frame) value = clampd(value * kAperiodicityScale, 0.0, 1.0);
  }

  std::vector<double> y(length, 0.0);
  std::vector<const double*> spConst(spRows.begin(), spRows.end());
  std::vector<const double*> apConst(apRows.begin(), apRows.end());
  Synthesis(f0.data(), f0Length, spConst.data(), apConst.data(), fftSize,
            kFramePeriodMs, sample_rate, static_cast<int>(length), y.data());
  applySignatureAndEnergy(y, energy_scale);

  std::vector<float> output(length);
  for (std::size_t i = 0; i < length; ++i) output[i] = static_cast<float>(y[i]);
  return output;
}

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
    std::size_t output_capacity) {
  if (output == nullptr || output_capacity < length) return -1;
  try {
    const auto result = amora_voice::Process(
        input,
        length,
        sample_rate,
        pitch_range_scale,
        f0_register_scale,
        energy_scale,
        softness,
        firmness);
    if (result.size() != length) return -2;
    std::copy(result.begin(), result.end(), output);
    return static_cast<int>(result.size());
  } catch (...) {
    return -3;
  }
}

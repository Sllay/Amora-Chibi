package dev.amora.chibi.audio

import kotlin.math.sqrt

/**
 * Local barge-in gate used while an Amora voice turn is active (synthesis or playback).
 *
 * It expects echo-cancelled/noise-suppressed microphone PCM, learns the residual floor
 * briefly, then requires sustained human-like energy above that floor. This detector
 * is intentionally faster than the normal VAD so a short "para" can cancel synthesis
 * before the next TTS segment is ready.
 */
class BargeInDetector(
    private val sampleRate: Int = 16_000,
    private val graceMs: Int = 180,
    private val sustainMs: Int = 140,
) {
    private var speechStartedNanos = 0L
    private var residualFloor = 0.009F
    private var aboveSamples = 0
    private var latched = false

    fun speechStarted(nowNanos: Long = System.nanoTime()) {
        speechStartedNanos = nowNanos
        residualFloor = 0.009F
        aboveSamples = 0
        latched = false
    }

    fun reset() {
        speechStartedNanos = 0L
        residualFloor = 0.009F
        aboveSamples = 0
        latched = false
    }

    fun accept(samples: FloatArray, nowNanos: Long = System.nanoTime()): Boolean {
        if (latched || speechStartedNanos == 0L || samples.isEmpty()) return false
        val rms = rms(samples)
        val elapsedMs = (nowNanos - speechStartedNanos) / 1_000_000L
        if (elapsedMs < graceMs) {
            residualFloor = (residualFloor * 0.84F + rms * 0.16F).coerceIn(0.0035F, 0.030F)
            return false
        }

        // Physical A15 feedback showed the old 0.038 / 2.55x gate was too hard
        // to cross for normal close-range speech. Keep a residual-adaptive floor,
        // but permit short commands without making a single wind spike sufficient.
        val threshold = maxOf(0.022F, residualFloor * 2.20F).coerceAtMost(0.085F)
        if (rms >= threshold) {
            aboveSamples += samples.size
        } else {
            residualFloor = (residualFloor * 0.975F + rms * 0.025F).coerceIn(0.0035F, 0.030F)
            aboveSamples = maxOf(0, aboveSamples - samples.size)
        }

        val required = sampleRate * sustainMs / 1000
        if (aboveSamples >= required) {
            latched = true
            return true
        }
        return false
    }

    private fun rms(samples: FloatArray): Float {
        var sum = 0.0
        for (sample in samples) {
            val value = sample.toDouble()
            sum += value * value
        }
        return sqrt(sum / samples.size).toFloat()
    }
}

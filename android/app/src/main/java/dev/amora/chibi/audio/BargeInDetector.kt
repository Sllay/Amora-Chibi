package dev.amora.chibi.audio

import kotlin.math.sqrt

/**
 * Local barge-in gate used only while Amora is speaking.
 *
 * It expects echo-cancelled microphone PCM, learns a small residual playback floor,
 * then requires sustained energy above that floor. It never authenticates a speaker
 * and never sends Amora's own playback to STT.
 */
class BargeInDetector(
    private val sampleRate: Int = 16_000,
    private val graceMs: Int = 360,
    private val sustainMs: Int = 240,
) {
    private var speechStartedNanos = 0L
    private var residualFloor = 0.012F
    private var aboveSamples = 0
    private var latched = false

    fun speechStarted(nowNanos: Long = System.nanoTime()) {
        speechStartedNanos = nowNanos
        residualFloor = 0.012F
        aboveSamples = 0
        latched = false
    }

    fun reset() {
        speechStartedNanos = 0L
        residualFloor = 0.012F
        aboveSamples = 0
        latched = false
    }

    fun accept(samples: FloatArray, nowNanos: Long = System.nanoTime()): Boolean {
        if (latched || speechStartedNanos == 0L || samples.isEmpty()) return false
        val rms = rms(samples)
        val elapsedMs = (nowNanos - speechStartedNanos) / 1_000_000L
        if (elapsedMs < graceMs) {
            residualFloor = (residualFloor * 0.82F + rms * 0.18F).coerceIn(0.004F, 0.035F)
            return false
        }

        val threshold = maxOf(0.038F, residualFloor * 2.55F).coerceAtMost(0.12F)
        if (rms >= threshold) {
            aboveSamples += samples.size
        } else {
            residualFloor = (residualFloor * 0.97F + rms * 0.03F).coerceIn(0.004F, 0.035F)
            aboveSamples = maxOf(0, aboveSamples - samples.size * 2)
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

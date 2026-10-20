package dev.amora.chibi.audio

import kotlin.math.sqrt

/**
 * Local barge-in gate used while an Amora voice turn is active (synthesis or playback).
 *
 * The microphone path already uses VOICE_COMMUNICATION + AEC + NS. This gate therefore
 * treats a sustained rise above the residual playback floor as a human interruption.
 * It is intentionally fast enough for short commands such as "para"/"não" while still
 * requiring more than a single wind/contact spike.
 */
class BargeInDetector(
    private val sampleRate: Int = 16_000,
    private val graceMs: Int = 120,
    private val sustainMs: Int = 90,
) {
    private var speechStartedNanos = 0L
    private var residualFloor = 0.007F
    private var aboveSamples = 0
    private var latched = false

    fun speechStarted(nowNanos: Long = System.nanoTime()) {
        speechStartedNanos = nowNanos
        residualFloor = 0.007F
        aboveSamples = 0
        latched = false
    }

    fun reset() {
        speechStartedNanos = 0L
        residualFloor = 0.007F
        aboveSamples = 0
        latched = false
    }

    fun accept(samples: FloatArray, nowNanos: Long = System.nanoTime()): Boolean {
        if (latched || speechStartedNanos == 0L || samples.isEmpty()) return false
        val rms = rms(samples)
        val elapsedMs = (nowNanos - speechStartedNanos) / 1_000_000L
        if (elapsedMs < graceMs) {
            // Learn the AEC residual briefly, but do not let loud speaker leakage push
            // the floor so high that BK has to shout over Amora to interrupt her.
            residualFloor = (residualFloor * 0.90F + rms * 0.10F).coerceIn(0.0035F, 0.022F)
            return false
        }

        // dev13 physical feedback: 0.022 / 2.20x still missed repeated close-range
        // speech on the A15. With AEC/NS and AGC active we can use a lower, bounded
        // adaptive threshold. 90 ms of sustained energy still rejects short spikes.
        val threshold = maxOf(0.012F, residualFloor * 1.55F).coerceAtMost(0.045F)
        if (rms >= threshold) {
            aboveSamples += samples.size
        } else {
            // Track room/playback residual slowly so speech itself does not raise the
            // gate while the user is in the middle of an interruption.
            residualFloor = (residualFloor * 0.990F + rms * 0.010F).coerceIn(0.0035F, 0.022F)
            aboveSamples = maxOf(0, aboveSamples - samples.size / 2)
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

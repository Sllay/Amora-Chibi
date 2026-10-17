package dev.amora.chibi.voice

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sqrt
import kotlin.math.tanh

/** Lightweight deterministic post-TTS coloration for AMORA_CANONICAL. */
object VoiceSignatureProcessor {
    fun process(
        source: FloatArray,
        sampleRate: Int,
        settings: VoiceProfileConfig.Effective,
    ): FloatArray {
        require(sampleRate > 0)
        if (source.isEmpty()) return source
        val samples = source.copyOf()

        // Broad tonal shaping: warmth, conversational presence and a little air.
        Biquad.lowShelf(sampleRate, 180f, settings.lowShelfDb).processInPlace(samples)
        Biquad.peaking(sampleRate, 2_700f, 0.8f, settings.presenceDb).processInPlace(samples)
        Biquad.highShelf(sampleRate, 7_200f.coerceAtMost(sampleRate * 0.38f), settings.airDb).processInPlace(samples)

        // Parallel, tiny odd-harmonic texture. It stays deliberately subtle so
        // the result reads as fantasy coloration rather than metallic/vocoder.
        val saturation = settings.softSaturation
        val texture = settings.fantasyTexture
        for (index in samples.indices) {
            val x = samples[index].coerceIn(-1f, 1f)
            val saturated = if (saturation > 0f) {
                val drive = 1.0 + saturation * 5.0
                (tanh(x.toDouble() * drive) / tanh(drive)).toFloat()
            } else x
            val harmonic = (saturated * abs(saturated)).coerceIn(-1f, 1f)
            samples[index] = (saturated * (1f - texture) + harmonic * texture)
        }

        compressInPlace(
            samples,
            sampleRate,
            threshold = settings.compressorThreshold,
            ratio = settings.compressorRatio,
            attackMs = settings.compressorAttackMs,
            releaseMs = settings.compressorReleaseMs,
        )

        for (index in samples.indices) {
            samples[index] = (samples[index] * settings.outputGain)
                .coerceIn(-settings.limiter, settings.limiter)
        }
        return samples
    }

    private fun compressInPlace(
        samples: FloatArray,
        sampleRate: Int,
        threshold: Float,
        ratio: Float,
        attackMs: Float,
        releaseMs: Float,
    ) {
        val attack = exp(-1.0 / (sampleRate * attackMs / 1000.0)).toFloat()
        val release = exp(-1.0 / (sampleRate * releaseMs / 1000.0)).toFloat()
        var envelope = 0f
        var gain = 1f
        for (index in samples.indices) {
            val level = abs(samples[index])
            envelope = if (level > envelope) {
                attack * envelope + (1f - attack) * level
            } else {
                release * envelope + (1f - release) * level
            }
            val targetGain = if (envelope > threshold && envelope > 0f) {
                val compressed = threshold + (envelope - threshold) / ratio
                (compressed / envelope).coerceIn(0f, 1f)
            } else 1f
            // Gain follows attenuation quickly and recovery more gently.
            gain = if (targetGain < gain) {
                attack * gain + (1f - attack) * targetGain
            } else {
                release * gain + (1f - release) * targetGain
            }
            samples[index] *= gain
        }
    }

    private class Biquad(
        private val b0: Double,
        private val b1: Double,
        private val b2: Double,
        private val a1: Double,
        private val a2: Double,
    ) {
        private var z1 = 0.0
        private var z2 = 0.0

        fun processInPlace(samples: FloatArray) {
            for (index in samples.indices) {
                val input = samples[index].toDouble()
                val output = b0 * input + z1
                z1 = b1 * input - a1 * output + z2
                z2 = b2 * input - a2 * output
                samples[index] = output.coerceIn(-2.0, 2.0).toFloat()
            }
        }

        companion object {
            fun peaking(sampleRate: Int, frequency: Float, q: Float, gainDb: Float): Biquad {
                val f = frequency.coerceIn(40f, sampleRate * 0.45f)
                val a = 10.0.pow(gainDb / 40.0)
                val w0 = 2.0 * PI * f / sampleRate
                val alpha = sin(w0) / (2.0 * q.coerceAtLeast(0.1f))
                val b0 = 1.0 + alpha * a
                val b1 = -2.0 * cos(w0)
                val b2 = 1.0 - alpha * a
                val a0 = 1.0 + alpha / a
                val a1 = -2.0 * cos(w0)
                val a2 = 1.0 - alpha / a
                return normalized(b0, b1, b2, a0, a1, a2)
            }

            fun lowShelf(sampleRate: Int, frequency: Float, gainDb: Float): Biquad =
                shelf(sampleRate, frequency, gainDb, high = false)

            fun highShelf(sampleRate: Int, frequency: Float, gainDb: Float): Biquad =
                shelf(sampleRate, frequency, gainDb, high = true)

            private fun shelf(sampleRate: Int, frequency: Float, gainDb: Float, high: Boolean): Biquad {
                val f = frequency.coerceIn(40f, sampleRate * 0.45f)
                val a = 10.0.pow(gainDb / 40.0)
                val w0 = 2.0 * PI * f / sampleRate
                val cosW = cos(w0)
                val sinW = sin(w0)
                val alpha = sinW / 2.0 * sqrt(2.0)
                val twoSqrtAAlpha = 2.0 * sqrt(a) * alpha
                val b0: Double
                val b1: Double
                val b2: Double
                val a0: Double
                val a1: Double
                val a2: Double
                if (high) {
                    b0 = a * ((a + 1) + (a - 1) * cosW + twoSqrtAAlpha)
                    b1 = -2 * a * ((a - 1) + (a + 1) * cosW)
                    b2 = a * ((a + 1) + (a - 1) * cosW - twoSqrtAAlpha)
                    a0 = (a + 1) - (a - 1) * cosW + twoSqrtAAlpha
                    a1 = 2 * ((a - 1) - (a + 1) * cosW)
                    a2 = (a + 1) - (a - 1) * cosW - twoSqrtAAlpha
                } else {
                    b0 = a * ((a + 1) - (a - 1) * cosW + twoSqrtAAlpha)
                    b1 = 2 * a * ((a - 1) - (a + 1) * cosW)
                    b2 = a * ((a + 1) - (a - 1) * cosW - twoSqrtAAlpha)
                    a0 = (a + 1) + (a - 1) * cosW + twoSqrtAAlpha
                    a1 = -2 * ((a - 1) + (a + 1) * cosW)
                    a2 = (a + 1) + (a - 1) * cosW - twoSqrtAAlpha
                }
                return normalized(b0, b1, b2, a0, a1, a2)
            }

            private fun normalized(
                b0: Double,
                b1: Double,
                b2: Double,
                a0: Double,
                a1: Double,
                a2: Double,
            ): Biquad = Biquad(b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)
        }
    }
}

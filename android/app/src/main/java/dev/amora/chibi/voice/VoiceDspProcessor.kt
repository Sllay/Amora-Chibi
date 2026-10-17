package dev.amora.chibi.voice

import kotlin.math.PI
import kotlin.math.pow
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.tanh

/**
 * Small, deterministic PCM effect chain used only by Voice Lab experiments.
 *
 * It deliberately avoids a heavyweight native DSP dependency. Pitch is created by
 * changing playback sample rate while compensating TTS speed before synthesis;
 * digital texture is a bounded mix of sample-hold, bit-depth quantization, drive,
 * and low-depth ring modulation.
 */
object VoiceDspProcessor {
    data class Plan(
        val synthesisSpeed: Float,
        val playbackSampleRate: Int,
        val pitchFactor: Float,
    )

    fun plan(
        requestedSpeed: Float,
        sourceSampleRate: Int,
        dsp: VoiceDspProfile,
    ): Plan {
        require(sourceSampleRate in 8_000..96_000) { "sampleRate inválido: $sourceSampleRate" }
        val pitchFactor = 2.0.pow(dsp.pitchSemitones.toDouble() / 12.0).toFloat()
        val playbackSampleRate = (sourceSampleRate * pitchFactor)
            .roundToInt()
            .coerceIn(8_000, 96_000)
        val synthesisSpeed = (requestedSpeed / pitchFactor).coerceIn(0.7f, 1.3f)
        return Plan(
            synthesisSpeed = synthesisSpeed,
            playbackSampleRate = playbackSampleRate,
            pitchFactor = pitchFactor,
        )
    }

    fun process(
        source: FloatArray,
        sourceSampleRate: Int,
        dsp: VoiceDspProfile,
    ): FloatArray {
        if (source.isEmpty()) return source
        val isNeutral = dsp.drive == 1f &&
            dsp.bitDepth == 16 &&
            dsp.sampleHold == 1 &&
            dsp.digitalMix == 0f &&
            dsp.ringMix == 0f
        if (isNeutral) return source

        val output = FloatArray(source.size)
        val levels = ((1 shl (dsp.bitDepth - 1)) - 1).coerceAtLeast(1).toFloat()
        val driveNorm = tanh(dsp.drive.toDouble()).toFloat().coerceAtLeast(0.0001f)
        val hold = dsp.sampleHold.coerceAtLeast(1)
        val digitalMix = dsp.digitalMix.coerceIn(0f, 0.8f)
        val ringMix = dsp.ringMix.coerceIn(0f, 0.25f)
        val ringRadiansPerSample = if (dsp.ringModHz > 0f && ringMix > 0f) {
            (2.0 * PI * dsp.ringModHz.toDouble()) / sourceSampleRate.toDouble()
        } else {
            0.0
        }

        var heldDigital = 0f
        for (index in source.indices) {
            val dry = source[index].coerceIn(-1f, 1f)
            val driven = (tanh((dry * dsp.drive).toDouble()).toFloat() / driveNorm).coerceIn(-1f, 1f)

            if (index % hold == 0) {
                heldDigital = (driven * levels).roundToInt().toFloat() / levels
            }
            var wet = driven * (1f - digitalMix) + heldDigital * digitalMix

            if (ringRadiansPerSample > 0.0) {
                val carrier = sin(index * ringRadiansPerSample).toFloat()
                val ringed = wet * carrier
                wet = wet * (1f - ringMix) + ringed * ringMix
            }
            output[index] = wet.coerceIn(-1f, 1f)
        }
        return output
    }
}

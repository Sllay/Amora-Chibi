package dev.amora.chibi.voice

/**
 * Native FINAL_B + VoiceSignature V1 processor.
 *
 * This is intentionally separate from the old VoiceDspProcessor. The old DSP
 * changes playback sample rate for pitch and was rejected for Amora's identity.
 */
object AmoraVoiceNative {
    init {
        System.loadLibrary("amora_voice_native")
    }

    external fun process(
        samples: FloatArray,
        sampleRate: Int,
        pitchRangeScale: Float,
        f0RegisterScale: Float,
        energyScale: Float,
        softness: Float,
        firmness: Float,
    ): FloatArray
}

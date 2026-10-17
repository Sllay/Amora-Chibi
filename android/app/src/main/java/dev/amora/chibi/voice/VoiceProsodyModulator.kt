package dev.amora.chibi.voice

/** Continuous affect coming from Amora's Core, not from the TTS provider. */
data class VoiceEmotion(
    val valence: Float = 0f,
    val arousal: Float = 0f,
    val dominance: Float = 0f,
) {
    fun bounded(): VoiceEmotion = VoiceEmotion(
        valence = valence.coerceIn(-1f, 1f),
        arousal = arousal.coerceIn(0f, 1f),
        dominance = dominance.coerceIn(-1f, 1f),
    )
}

data class EffectiveVoiceTuning(
    val speed: Float,
    val silenceScale: Float,
    val numSteps: Int,
    val outputGain: Float,
)

/**
 * Keeps emotion as a small modulation around the selected voice profile.
 * Identity/profile wins; emotion is bounded so an intense state cannot turn
 * Amora into a completely different speaker.
 */
object VoiceProsodyModulator {
    fun apply(
        profile: VoiceProfile,
        emotion: VoiceEmotion,
        modulation: EmotionVoiceModulation,
    ): EffectiveVoiceTuning {
        if (!modulation.enabled) {
            return EffectiveVoiceTuning(
                speed = profile.speed,
                silenceScale = profile.silenceScale,
                numSteps = profile.numSteps,
                outputGain = profile.outputGain,
            )
        }

        val affect = emotion.bounded()
        val speedFactor = 1f + affect.arousal * modulation.speedArousal + affect.valence * modulation.speedValence
        val silenceFactor = 1f + affect.arousal * modulation.silenceArousal + affect.valence * modulation.silenceValence
        val gainFactor = 1f + affect.arousal * modulation.gainArousal + affect.valence * modulation.gainValence

        return EffectiveVoiceTuning(
            speed = (profile.speed * speedFactor).coerceIn(modulation.speedMin, modulation.speedMax),
            silenceScale = (profile.silenceScale * silenceFactor).coerceIn(modulation.silenceMin, modulation.silenceMax),
            numSteps = profile.numSteps,
            outputGain = (profile.outputGain * gainFactor).coerceIn(modulation.gainMin, modulation.gainMax),
        )
    }
}

enum class LabEmotionPreset(
    val label: String,
    val emotion: VoiceEmotion,
) {
    NEUTRAL("NEUTRA", VoiceEmotion()),
    HAPPY("FELIZ", VoiceEmotion(valence = 0.7f, arousal = 0.55f, dominance = 0.2f)),
    TENDER("CARINHOSA", VoiceEmotion(valence = 0.8f, arousal = 0.18f, dominance = 0.0f)),
    ANGRY("BRAVA", VoiceEmotion(valence = -0.65f, arousal = 0.85f, dominance = 0.65f)),
    SAD("TRISTE", VoiceEmotion(valence = -0.7f, arousal = 0.10f, dominance = -0.35f)),
}

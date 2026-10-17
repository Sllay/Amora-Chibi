package dev.amora.chibi.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceProsodyModulatorTest {
    private val profile = VoiceProfile(
        id = "TEST",
        label = "TEST",
        description = "",
        speed = 1.08f,
        silenceScale = 0.9f,
        numSteps = 6,
        outputGain = 0.88f,
    )

    private val modulation = EmotionVoiceModulation(
        enabled = true,
        speedArousal = 0.06f,
        speedValence = 0.015f,
        silenceArousal = -0.08f,
        silenceValence = -0.02f,
        gainArousal = 0.04f,
        gainValence = 0.01f,
        speedMin = 0.85f,
        speedMax = 1.15f,
        silenceMin = 0.75f,
        silenceMax = 1.2f,
        gainMin = 0.75f,
        gainMax = 1.0f,
    )

    @Test
    fun emotionCanOnlyModulateInsideConfiguredBounds() {
        val tuning = VoiceProsodyModulator.apply(
            profile = profile,
            emotion = VoiceEmotion(valence = 99f, arousal = 99f, dominance = 99f),
            modulation = modulation,
        )

        assertTrue(tuning.speed in modulation.speedMin..modulation.speedMax)
        assertTrue(tuning.silenceScale in modulation.silenceMin..modulation.silenceMax)
        assertTrue(tuning.outputGain in modulation.gainMin..modulation.gainMax)
        assertEquals(profile.numSteps, tuning.numSteps)
    }

    @Test
    fun disabledEmotionReturnsProfileUnchanged() {
        val tuning = VoiceProsodyModulator.apply(
            profile = profile,
            emotion = LabEmotionPreset.ANGRY.emotion,
            modulation = modulation.copy(enabled = false),
        )

        assertEquals(profile.speed, tuning.speed, 0.0001f)
        assertEquals(profile.silenceScale, tuning.silenceScale, 0.0001f)
        assertEquals(profile.outputGain, tuning.outputGain, 0.0001f)
    }
}

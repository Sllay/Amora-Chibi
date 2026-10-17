package dev.amora.chibi.voice

import kotlin.math.sin
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceProfileConfigTest {
    private val identity = VoiceIdentity.AMORA_V1

    @Test
    fun safeDefaultKeepsBrainProsodyBoundedWithoutChangingIdentity() {
        val profile = VoiceProfileConfig.safeDefault(identity)
        val calm = profile.effective(
            requestedRate = 0.1f,
            requestedPitch = 0.9f,
            valence = -1f,
            arousal = -1f,
        )
        val excited = profile.effective(
            requestedRate = 4f,
            requestedPitch = 1.1f,
            valence = 1f,
            arousal = 1f,
        )

        assertEquals(profile.prosody.minSpeed, calm.speed, 0.0001f)
        assertEquals(profile.prosody.maxSpeed, excited.speed, 0.0001f)
        assertTrue(excited.presenceDb > calm.presenceDb)
        assertTrue(excited.airDb > calm.airDb)
        assertEquals(0, profile.base.speakerId)
    }

    @Test
    fun expressionProfilesActuallyProduceDifferentTuning() {
        val profile = VoiceProfileConfig.safeDefault(identity)
        val natural = profile.profiles.getValue("NATURAL")
        val soft = profile.profiles.getValue("SOFT")
        val synth = profile.profiles.getValue("SYNTH")

        assertNotEquals(natural.speedScale, soft.speedScale)
        assertTrue(soft.airDeltaDb > natural.airDeltaDb)
        assertTrue(synth.textureScale > natural.textureScale)
        assertTrue(synth.saturationScale > natural.saturationScale)
    }

    @Test
    fun voiceSignatureIsDeterministicFiniteAndLimited() {
        val profile = VoiceProfileConfig.safeDefault(identity)
        val settings = profile.effective(1f, 1f, 0f, 0f)
        val input = FloatArray(4_800) { index -> (0.35 * sin(index * 0.031)).toFloat() }

        val first = VoiceSignatureProcessor.process(input, 24_000, settings)
        val second = VoiceSignatureProcessor.process(input, 24_000, settings)

        assertEquals(first.size, second.size)
        assertTrue(first.indices.all { first[it].isFinite() && second[it].isFinite() })
        assertTrue(first.indices.all { first[it] == second[it] })
        assertTrue(first.maxOf { kotlin.math.abs(it) } <= settings.limiter + 0.0001f)
        assertTrue(first.indices.any { kotlin.math.abs(first[it] - input[it]) > 0.00001f })
    }
}

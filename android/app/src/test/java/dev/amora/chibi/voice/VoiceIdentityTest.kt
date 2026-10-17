package dev.amora.chibi.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceIdentityTest {
    private val voice = VoiceIdentity.AMORA_V1

    @Test
    fun canonicalVoiceIsPhysicallySelectedSupertonicSpeakerZero() {
        assertEquals("pt-BR", voice.localeTag)
        assertEquals("sherpa-onnx-supertonic-3-tts-int8-2026-05-11", voice.packId)
        assertEquals("supertonic3-int8", voice.backendId)
        assertEquals(0, voice.speakerId)
        assertEquals("pt", voice.language)
        assertEquals(2, voice.numThreads)
        assertTrue(voice.requiredFiles.contains("voice.bin"))
        assertTrue(voice.requiredFiles.contains("vocoder.int8.onnx"))
    }

    @Test
    fun canonicalIdentityIsSeparateFromHotTuningProfile() {
        val profile = VoiceProfileConfig.safeDefault(voice)
        assertEquals("amora-voice-v1", profile.profileId)
        assertEquals("NATURAL", profile.activeProfile)
        assertEquals(voice.backendId, profile.base.backend)
        assertEquals(voice.speakerId, profile.base.speakerId)
    }
}

package dev.amora.chibi.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceProfileCatalogTest {
    @Test
    fun builtinCatalogKeepsLabNonCanonical() {
        val catalog = VoiceProfileCatalog.builtIn()

        assertEquals(1, catalog.schemaVersion)
        assertEquals("supertonic-3-int8", catalog.backendId)
        assertEquals("pt", catalog.language)
        assertFalse(catalog.canonical)
        assertTrue(catalog.profiles.any { it.id == "NATURAL" })
        assertTrue(catalog.profiles.any { it.id == "AMORA_SOFT" })
        assertTrue(catalog.profiles.any { it.id == "AMORA_SYNTH" })
        assertTrue(catalog.profiles.any { it.id == "CHIBI" })
    }

    @Test
    fun emotionCanOnlyModulateInsideConfiguredBounds() {
        val catalog = VoiceProfileCatalog.builtIn()
        val profile = catalog.profile("CHIBI")!!
        val tuning = VoiceProsodyModulator.apply(
            profile = profile,
            emotion = VoiceEmotion(valence = 99f, arousal = 99f, dominance = 99f),
            modulation = catalog.emotionModulation,
        )

        assertTrue(tuning.speed in catalog.emotionModulation.speedMin..catalog.emotionModulation.speedMax)
        assertTrue(tuning.silenceScale in catalog.emotionModulation.silenceMin..catalog.emotionModulation.silenceMax)
        assertTrue(tuning.outputGain in catalog.emotionModulation.gainMin..catalog.emotionModulation.gainMax)
        assertEquals(profile.numSteps, tuning.numSteps)
    }

    @Test(expected = IllegalArgumentException::class)
    fun rejectsUnsupportedCatalogSchema() {
        VoiceProfileCatalog.parse(
            """{
              "schema_version": 99,
              "revision": "bad",
              "backend": {"id":"x","model_id":"x","language":"pt","sample_rate_hz":24000},
              "lab": {"default_speaker_id":0,"default_profile_id":"NATURAL","canonical":false},
              "profiles": [{"id":"NATURAL","label":"NATURAL","speed":1.0,"silence_scale":1.0,"num_steps":8,"output_gain":0.9}],
              "emotion_modulation": {}
            }""",
        )
    }
}

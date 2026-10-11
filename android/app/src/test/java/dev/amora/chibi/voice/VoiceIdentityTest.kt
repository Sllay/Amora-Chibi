package dev.amora.chibi.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceIdentityTest {
    private val voice = VoiceIdentity.AMORA_V1_PROVISIONAL

    @Test
    fun canonicalVoiceIsBrazilianPortugueseAndPinned() {
        assertEquals("pt-BR", voice.localeTag)
        assertEquals("vits-piper-pt_BR-faber-medium", voice.packId)
        assertEquals("pt_BR-faber-medium.onnx", voice.modelFileName)
        assertEquals(64, voice.expectedModelSha256.length)
        assertTrue(voice.downloadUrl.startsWith("https://"))
    }

    @Test
    fun prosodyRateCannotMutateTheVoiceOutsideItsSafeWindow() {
        assertEquals(voice.minSpeed, voice.speedFor(0.1f), 0.0001f)
        assertEquals(1.0f, voice.speedFor(1.0f), 0.0001f)
        assertEquals(voice.maxSpeed, voice.speedFor(4.0f), 0.0001f)
    }
}

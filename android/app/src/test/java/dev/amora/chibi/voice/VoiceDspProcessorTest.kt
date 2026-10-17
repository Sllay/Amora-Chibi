package dev.amora.chibi.voice

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceDspProcessorTest {
    @Test
    fun neutralDspLeavesSamplesAndRateUntouched() {
        val source = floatArrayOf(-0.5f, 0f, 0.25f, 0.75f)
        val dsp = VoiceDspProfile()
        val plan = VoiceDspProcessor.plan(1.0f, 24_000, dsp)
        val output = VoiceDspProcessor.process(source, 24_000, dsp)

        assertEquals(1.0f, plan.synthesisSpeed, 0.0001f)
        assertEquals(24_000, plan.playbackSampleRate)
        assertArrayEquals(source, output, 0.0001f)
    }

    @Test
    fun positivePitchRaisesPlaybackRateAndCompensatesSynthesisSpeed() {
        val dsp = VoiceDspProfile(pitchSemitones = 4f)
        val plan = VoiceDspProcessor.plan(1.0f, 24_000, dsp)

        assertTrue(plan.playbackSampleRate > 24_000)
        assertTrue(plan.synthesisSpeed < 1.0f)
        assertTrue(plan.pitchFactor > 1.0f)
    }

    @Test
    fun digitalTextureActuallyChangesPcmButStaysBounded() {
        val source = FloatArray(128) { index -> ((index % 17) - 8) / 8f }
        val dsp = VoiceDspProfile(
            drive = 1.25f,
            bitDepth = 10,
            sampleHold = 2,
            digitalMix = 0.45f,
            ringModHz = 52f,
            ringMix = 0.1f,
        )
        val output = VoiceDspProcessor.process(source, 24_000, dsp)

        assertTrue(output.indices.any { output[it] != source[it] })
        assertTrue(output.all { it in -1f..1f })
    }
}

package dev.amora.chibi.audio

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BargeInDetectorTest {
    @Test
    fun ignoresResidualPlaybackThenAcceptsNormalCloseRangeSpeech() {
        val detector = BargeInDetector(sampleRate = 16_000, graceMs = 120, sustainMs = 90)
        val start = 1_000_000_000L
        detector.speechStarted(start)
        val residual = FloatArray(160) { 0.011F }
        repeat(12) { index ->
            assertFalse(detector.accept(residual, start + (index + 1) * 10_000_000L))
        }
        val voice = FloatArray(160) { if (it % 2 == 0) 0.035F else -0.035F }
        var triggered = false
        repeat(12) { index ->
            if (detector.accept(voice, start + 130_000_000L + index * 10_000_000L)) triggered = true
        }
        assertTrue(triggered)
    }

    @Test
    fun shortQuietVoiceCommandCanTakeTheFloor() {
        val detector = BargeInDetector(sampleRate = 16_000, graceMs = 0, sustainMs = 90)
        val start = 2_000_000_000L
        detector.speechStarted(start)
        val voice = FloatArray(160) { if (it % 2 == 0) 0.030F else -0.030F }
        var triggered = false
        repeat(11) { index ->
            if (detector.accept(voice, start + (index + 1) * 10_000_000L)) triggered = true
        }
        assertTrue(triggered)
    }

    @Test
    fun briefWindLikeSpikeDoesNotTakeTheFloor() {
        val detector = BargeInDetector(sampleRate = 16_000, graceMs = 0, sustainMs = 90)
        val start = 3_000_000_000L
        detector.speechStarted(start)
        val spike = FloatArray(160) { 0.20F }
        repeat(5) { index ->
            assertFalse(detector.accept(spike, start + (index + 1) * 10_000_000L))
        }
        val room = FloatArray(160) { 0.008F }
        repeat(12) { index ->
            assertFalse(detector.accept(room, start + 60_000_000L + index * 10_000_000L))
        }
    }
}

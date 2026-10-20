package dev.amora.chibi.audio

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BargeInDetectorTest {
    @Test
    fun ignoresResidualPlaybackThenAcceptsSustainedHumanEnergy() {
        val detector = BargeInDetector(sampleRate = 16_000, graceMs = 180, sustainMs = 140)
        val start = 1_000_000_000L
        detector.speechStarted(start)
        val residual = FloatArray(160) { 0.010F }
        repeat(18) { index ->
            assertFalse(detector.accept(residual, start + (index + 1) * 10_000_000L))
        }
        val voice = FloatArray(160) { if (it % 2 == 0) 0.10F else -0.10F }
        var triggered = false
        repeat(18) { index ->
            if (detector.accept(voice, start + 190_000_000L + index * 10_000_000L)) triggered = true
        }
        assertTrue(triggered)
    }

    @Test
    fun shortVoiceCommandCanTakeTheFloor() {
        val detector = BargeInDetector(sampleRate = 16_000, graceMs = 0, sustainMs = 140)
        val start = 2_000_000_000L
        detector.speechStarted(start)
        val voice = FloatArray(160) { if (it % 2 == 0) 0.065F else -0.065F }
        var triggered = false
        repeat(16) { index ->
            if (detector.accept(voice, start + (index + 1) * 10_000_000L)) triggered = true
        }
        assertTrue(triggered)
    }

    @Test
    fun briefWindLikeSpikeDoesNotTakeTheFloor() {
        val detector = BargeInDetector(sampleRate = 16_000, graceMs = 0, sustainMs = 140)
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

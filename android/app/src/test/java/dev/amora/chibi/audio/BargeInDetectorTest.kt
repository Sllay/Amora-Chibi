package dev.amora.chibi.audio

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BargeInDetectorTest {
    @Test
    fun ignoresResidualPlaybackThenAcceptsSustainedHumanEnergy() {
        val detector = BargeInDetector(sampleRate = 16_000, graceMs = 300, sustainMs = 220)
        val start = 1_000_000_000L
        detector.speechStarted(start)
        val residual = FloatArray(160) { 0.012F }
        repeat(25) { index ->
            assertFalse(detector.accept(residual, start + (index + 1) * 10_000_000L))
        }
        val voice = FloatArray(160) { if (it % 2 == 0) 0.16F else -0.16F }
        var triggered = false
        repeat(35) { index ->
            if (detector.accept(voice, start + 400_000_000L + index * 10_000_000L)) triggered = true
        }
        assertTrue(triggered)
    }

    @Test
    fun shortSpikeDoesNotTakeTheFloor() {
        val detector = BargeInDetector(sampleRate = 16_000, graceMs = 0, sustainMs = 240)
        val start = 2_000_000_000L
        detector.speechStarted(start)
        val spike = FloatArray(160) { 0.20F }
        repeat(8) { index ->
            assertFalse(detector.accept(spike, start + (index + 1) * 10_000_000L))
        }
    }
}

package dev.amora.chibi.audio

import kotlin.math.sqrt

class EnergySpeechSegmenter(
    // This path is only a fallback when Sherpa is unavailable. It should prefer
    // missing a very quiet utterance over turning steady room noise into speech.
    private val threshold: Float = 0.030F,
    private val sampleRate: Int = 16_000,
) : SpeechSegmenter {
    private val active = ArrayList<Float>()
    private var speaking = false
    private var silentFrames = 0

    override fun accept(samples: FloatArray): List<FloatArray> {
        if (samples.isEmpty()) return emptyList()
        val rms = sqrt(samples.fold(0.0) { acc, value -> acc + value * value } / samples.size).toFloat()
        val speechNow = rms >= threshold
        if (speechNow) {
            speaking = true
            silentFrames = 0
        } else if (speaking) {
            silentFrames += 1
        }

        if (speaking) {
            for (sample in samples) active.add(sample)
        }

        val maxSamples = sampleRate * 12
        if (speaking && (silentFrames >= 6 || active.size >= maxSamples)) {
            val segment = active.toFloatArray()
            active.clear()
            speaking = false
            silentFrames = 0
            return if (segment.size >= sampleRate * 3 / 10) listOf(segment) else emptyList()
        }
        return emptyList()
    }

    override fun flush(): List<FloatArray> {
        if (active.isEmpty()) return emptyList()
        val segment = active.toFloatArray()
        active.clear()
        speaking = false
        silentFrames = 0
        return if (segment.size >= sampleRate * 3 / 10) listOf(segment) else emptyList()
    }

    override fun close() = Unit
}

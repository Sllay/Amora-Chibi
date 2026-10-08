package dev.amora.chibi.audio

interface SpeechSegmenter : AutoCloseable {
    fun accept(samples: FloatArray): List<FloatArray>
    fun flush(): List<FloatArray>
    override fun close()
}

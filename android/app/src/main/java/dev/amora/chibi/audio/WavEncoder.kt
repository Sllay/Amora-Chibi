package dev.amora.chibi.audio

import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.charset.StandardCharsets

object WavEncoder {
    fun pcm16(samples: FloatArray, sampleRate: Int = 16_000): ByteArray {
        val dataSize = samples.size * 2
        val out = ByteBuffer.allocate(44 + dataSize).order(ByteOrder.LITTLE_ENDIAN)
        out.put("RIFF".toByteArray(StandardCharsets.US_ASCII))
        out.putInt(36 + dataSize)
        out.put("WAVE".toByteArray(StandardCharsets.US_ASCII))
        out.put("fmt ".toByteArray(StandardCharsets.US_ASCII))
        out.putInt(16)
        out.putShort(1)
        out.putShort(1)
        out.putInt(sampleRate)
        out.putInt(sampleRate * 2)
        out.putShort(2)
        out.putShort(16)
        out.put("data".toByteArray(StandardCharsets.US_ASCII))
        out.putInt(dataSize)
        for (sample in samples) {
            val value = (sample.coerceIn(-1F, 1F) * 32767F).toInt().toShort()
            out.putShort(value)
        }
        return out.array()
    }
}

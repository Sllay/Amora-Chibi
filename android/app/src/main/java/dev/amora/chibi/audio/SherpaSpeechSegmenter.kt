package dev.amora.chibi.audio

import android.content.Context
import com.k2fsa.sherpa.onnx.SileroVadModelConfig
import com.k2fsa.sherpa.onnx.Vad
import com.k2fsa.sherpa.onnx.VadModelConfig

class SherpaSpeechSegmenter(context: Context) : SpeechSegmenter {
    private val vad = Vad(
        assetManager = context.assets,
        config = VadModelConfig(
            sileroVadModelConfig = SileroVadModelConfig(
                model = "silero_vad.onnx",
                // A15 VOICE_COMMUNICATION processing can soften the first vowel
                // of a phrase. A slightly lower onset threshold avoids clipping
                // vocatives such as "Amora" while still relying on Silero VAD.
                threshold = 0.38F,
                // Natural pt-BR speech contains brief intra-sentence pauses. The
                // previous 300 ms boundary fragmented ordinary conversation too
                // aggressively on the physical A15.
                minSilenceDuration = 0.55F,
                minSpeechDuration = 0.12F,
                windowSize = 512,
                maxSpeechDuration = 12.0F,
            ),
            sampleRate = 16_000,
            numThreads = 1,
            provider = "cpu",
            debug = false,
        ),
    )

    override fun accept(samples: FloatArray): List<FloatArray> {
        vad.acceptWaveform(samples)
        return drain()
    }

    override fun flush(): List<FloatArray> {
        vad.flush()
        return drain()
    }

    override fun close() {
        vad.release()
    }

    private fun drain(): List<FloatArray> {
        val output = mutableListOf<FloatArray>()
        while (!vad.empty()) {
            output += vad.front().samples.copyOf()
            vad.pop()
        }
        return output
    }
}

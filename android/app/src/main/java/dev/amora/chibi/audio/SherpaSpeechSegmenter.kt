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
                // The physical A15 showed that 0.38 was permissive enough for a
                // fan to become speech. Keep Silero near its normal operating
                // threshold and recover phrase onset with a longer speech-history
                // window instead of lowering the classifier threshold.
                threshold = 0.50F,
                // Natural pt-BR speech contains brief intra-sentence pauses.
                minSilenceDuration = 0.55F,
                // Sherpa's VAD keeps history around detected speech. A larger
                // minimum speech window helps preserve the leading vowel in
                // vocatives such as "Amora" while also rejecting tiny noise hits.
                minSpeechDuration = 0.35F,
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

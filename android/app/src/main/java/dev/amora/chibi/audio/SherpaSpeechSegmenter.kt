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
                threshold = 0.5F,
                minSilenceDuration = 0.30F,
                minSpeechDuration = 0.18F,
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

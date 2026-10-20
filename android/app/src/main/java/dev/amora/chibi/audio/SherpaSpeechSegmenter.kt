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
                // A15 physical feedback: 0.38 admitted fan noise while 0.50 made
                // normal close-range speech and short commands unnecessarily hard.
                // 0.44 keeps margin from the noisy setting without demanding slow,
                // over-articulated speech from BK.
                threshold = 0.44F,
                // End a turn sooner so the STT request starts promptly after BK
                // stops talking, while retaining ordinary pt-BR micro-pauses.
                minSilenceDuration = 0.42F,
                // Permit short commands/vocatives ("para", "Amora", "não") to
                // become real segments. Noise suppression + Silero confidence still
                // provide the main rejection gate for steady room noise.
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

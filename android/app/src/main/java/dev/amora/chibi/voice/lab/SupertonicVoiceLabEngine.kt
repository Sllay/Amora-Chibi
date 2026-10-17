package dev.amora.chibi.voice.lab

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.util.Log
import com.k2fsa.sherpa.onnx.GenerationConfig
import com.k2fsa.sherpa.onnx.OfflineTts
import com.k2fsa.sherpa.onnx.OfflineTtsConfig
import com.k2fsa.sherpa.onnx.OfflineTtsModelConfig
import com.k2fsa.sherpa.onnx.OfflineTtsSupertonicModelConfig
import kotlin.math.roundToInt

/**
 * Experimental Supertonic 3 voice laboratory.
 *
 * This class is intentionally isolated from LocalVoiceEngine: nothing selected
 * here can become Amora's production voice without an explicit promotion after
 * physical-device certification.
 */
class SupertonicVoiceLabEngine(context: Context) : AutoCloseable {
    enum class Preset(
        val label: String,
        val speed: Float,
        val silenceScale: Float,
        val numSteps: Int,
    ) {
        NATURAL("NATURAL", 1.00f, 1.00f, 8),
        AMORA_SOFT("AMORA_SOFT", 0.95f, 1.05f, 8),
        AMORA_SYNTH("AMORA_SYNTH", 0.92f, 0.90f, 5),
        CHIBI("CHIBI", 1.08f, 0.90f, 6),
    }

    data class Result(
        val speakerId: Int,
        val preset: Preset,
        val sampleRate: Int,
        val audioSeconds: Double,
        val synthesisSeconds: Double,
    ) {
        val rtf: Double
            get() = if (audioSeconds > 0.0) synthesisSeconds / audioSeconds else 0.0
    }

    private val appContext = context.applicationContext
    private val engineLock = Any()
    @Volatile private var engine: OfflineTts? = null
    @Volatile private var activeTrack: AudioTrack? = null

    fun assetsAvailable(): Boolean = REQUIRED_FILES.all { assetExists("$MODEL_ID/$it") }

    fun prepare(): Int = ensureEngine().numSpeakers().also {
        require(it > 0) { "Supertonic não informou speakers disponíveis" }
    }

    fun synthesizeAndPlay(
        text: String,
        speakerId: Int,
        preset: Preset,
        onStage: (String) -> Unit = {},
    ): Result {
        val cleanText = text.trim()
        require(cleanText.isNotEmpty()) { "Digite uma frase para sintetizar" }
        require(assetsAvailable()) { "assets do Supertonic 3 ausentes no APK" }

        val localEngine = ensureEngine()
        val speakers = localEngine.numSpeakers()
        require(speakers > 0) { "nenhum speaker disponível" }
        require(speakerId in 0 until speakers) {
            "speaker $speakerId inválido; disponíveis: 0..${speakers - 1}"
        }

        onStage("Sintetizando speaker $speakerId • ${preset.label}…")
        val startedNs = System.nanoTime()
        val audio = localEngine.generateWithConfig(
            text = cleanText,
            config = GenerationConfig(
                silenceScale = preset.silenceScale,
                speed = preset.speed,
                sid = speakerId,
                numSteps = preset.numSteps,
                extra = mapOf("lang" to "pt"),
            ),
        )
        val synthesisSeconds = (System.nanoTime() - startedNs) / 1_000_000_000.0
        require(audio.samples.isNotEmpty() && audio.sampleRate > 0) {
            "Supertonic retornou áudio vazio"
        }
        val audioSeconds = audio.samples.size.toDouble() / audio.sampleRate.toDouble()

        onStage("Reproduzindo ${"%.2f".format(audioSeconds)} s • ${audio.sampleRate} Hz…")
        playSamples(audio.samples, audio.sampleRate)

        val result = Result(
            speakerId = speakerId,
            preset = preset,
            sampleRate = audio.sampleRate,
            audioSeconds = audioSeconds,
            synthesisSeconds = synthesisSeconds,
        )
        Log.i(
            TAG,
            "Voice Lab speaker=%d preset=%s synth=%.0fms audio=%.2fs rtf=%.3f".format(
                result.speakerId,
                result.preset.label,
                result.synthesisSeconds * 1000.0,
                result.audioSeconds,
                result.rtf,
            ),
        )
        return result
    }

    fun stop() {
        activeTrack?.let { track ->
            runCatching { track.pause() }
            runCatching { track.flush() }
            runCatching { track.stop() }
        }
    }

    private fun ensureEngine(): OfflineTts {
        engine?.let { return it }
        synchronized(engineLock) {
            engine?.let { return it }
            require(assetsAvailable()) { "pacote $MODEL_ID incompleto" }

            val root = MODEL_ID
            val config = OfflineTtsConfig(
                model = OfflineTtsModelConfig(
                    supertonic = OfflineTtsSupertonicModelConfig(
                        durationPredictor = "$root/duration_predictor.int8.onnx",
                        textEncoder = "$root/text_encoder.int8.onnx",
                        vectorEstimator = "$root/vector_estimator.int8.onnx",
                        vocoder = "$root/vocoder.int8.onnx",
                        ttsJson = "$root/tts.json",
                        unicodeIndexer = "$root/unicode_indexer.bin",
                        voiceStyle = "$root/voice.bin",
                    ),
                    numThreads = 2,
                    debug = false,
                    provider = "cpu",
                ),
                maxNumSentences = 1,
                silenceScale = 1.0f,
            )
            return OfflineTts(assetManager = appContext.assets, config = config).also { created ->
                require(created.sampleRate() > 0) { "sampleRate inválido" }
                require(created.numSpeakers() > 0) { "nenhum speaker no voice.bin" }
                engine = created
                Log.i(TAG, "Supertonic Voice Lab pronto: ${created.numSpeakers()} speakers")
            }
        }
    }

    private fun playSamples(samples: FloatArray, sampleRate: Int) {
        stop()
        var track: AudioTrack? = null
        try {
            track = createTrack(sampleRate)
            activeTrack = track
            track.play()

            val buffer = ShortArray(PCM_CHUNK_SAMPLES)
            var sourceOffset = 0
            while (sourceOffset < samples.size && activeTrack === track) {
                val count = minOf(buffer.size, samples.size - sourceOffset)
                for (index in 0 until count) {
                    buffer[index] = (
                        samples[sourceOffset + index].coerceIn(-1f, 1f) *
                            OUTPUT_GAIN * Short.MAX_VALUE
                        ).roundToInt().toShort()
                }
                var writtenOffset = 0
                while (writtenOffset < count && activeTrack === track) {
                    val written = track.write(
                        buffer,
                        writtenOffset,
                        count - writtenOffset,
                        AudioTrack.WRITE_BLOCKING,
                    )
                    require(written > 0) { "AudioTrack.write retornou $written" }
                    writtenOffset += written
                }
                sourceOffset += writtenOffset
            }

            if (activeTrack === track && sourceOffset == samples.size) {
                waitForPlayback(track, sourceOffset, sampleRate)
            }
        } finally {
            if (activeTrack === track) activeTrack = null
            track?.let { localTrack ->
                runCatching { localTrack.stop() }
                runCatching { localTrack.release() }
            }
        }
    }

    private fun createTrack(sampleRate: Int): AudioTrack {
        val minBuffer = AudioTrack.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        require(minBuffer > 0) { "AudioTrack não suporta $sampleRate Hz" }
        val attributes = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_MEDIA)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .build()
        val format = AudioFormat.Builder()
            .setSampleRate(sampleRate)
            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .build()
        return AudioTrack(
            attributes,
            format,
            maxOf(minBuffer, sampleRate * 2),
            AudioTrack.MODE_STREAM,
            AudioManager.AUDIO_SESSION_ID_GENERATE,
        ).also {
            require(it.state == AudioTrack.STATE_INITIALIZED) { "AudioTrack não inicializou" }
        }
    }

    private fun waitForPlayback(track: AudioTrack, framesWritten: Int, sampleRate: Int) {
        val audioMillis = framesWritten * 1000L / sampleRate.coerceAtLeast(1)
        val deadline = System.nanoTime() + (audioMillis + PLAYBACK_GRACE_MS) * 1_000_000L
        while (activeTrack === track && System.nanoTime() < deadline) {
            if (track.playbackHeadPosition >= framesWritten) return
            try {
                Thread.sleep(8)
            } catch (_: InterruptedException) {
                Thread.currentThread().interrupt()
                return
            }
        }
    }

    private fun assetExists(path: String): Boolean = runCatching {
        appContext.assets.open(path).use { true }
    }.getOrDefault(false)

    override fun close() {
        stop()
        synchronized(engineLock) {
            runCatching { engine?.release() }
            engine = null
        }
    }

    companion object {
        const val MODEL_ID = "sherpa-onnx-supertonic-3-tts-int8-2026-05-11"
        private const val TAG = "AmoraVoiceLab"
        private const val PCM_CHUNK_SAMPLES = 4_096
        private const val PLAYBACK_GRACE_MS = 1_500L
        private const val OUTPUT_GAIN = 0.92f
        private val REQUIRED_FILES = listOf(
            "duration_predictor.int8.onnx",
            "text_encoder.int8.onnx",
            "vector_estimator.int8.onnx",
            "vocoder.int8.onnx",
            "tts.json",
            "unicode_indexer.bin",
            "voice.bin",
        )
    }
}

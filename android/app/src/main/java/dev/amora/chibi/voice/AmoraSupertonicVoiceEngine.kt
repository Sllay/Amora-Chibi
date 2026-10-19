package dev.amora.chibi.voice

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
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.roundToInt

/**
 * Android physical-gate candidate for Amora's approved voice stack.
 *
 * Pipeline:
 * custom Supertonic 60/40 style -> WORLD FINAL_B morphology ->
 * VoiceSignature V1 Soft -> Emotion/Prosody V2 -> AudioTrack.
 *
 * No old playback-rate pitch DSP is used. There is deliberately no hidden Faber
 * fallback: a failure must be visible during Galaxy A15 certification.
 */
class AmoraSupertonicVoiceEngine(
    context: Context,
    private val diagnostics: VoiceDiagnostics = VoiceDiagnostics(context),
    private val onStatusChanged: (VoiceDiagnostics.Snapshot) -> Unit = {},
) : AutoCloseable {
    enum class PlayResult { COMPLETED, UNAVAILABLE, FAILED, INTERRUPTED }

    data class Metrics(
        val expressionLabel: String,
        val synthesisSeconds: Double,
        val nativeSeconds: Double,
        val audioSeconds: Double,
    ) {
        val synthesisRtf: Double get() = if (audioSeconds > 0.0) synthesisSeconds / audioSeconds else 0.0
        val nativeRtf: Double get() = if (audioSeconds > 0.0) nativeSeconds / audioSeconds else 0.0
        val totalRtf: Double get() = synthesisRtf + nativeRtf
    }

    private val appContext = context.applicationContext
    private val engineLock = Any()
    @Volatile private var engine: OfflineTts? = null
    @Volatile private var activeTrack: AudioTrack? = null
    @Volatile private var lastFailure: String? = null
    @Volatile private var lastMetrics: Metrics? = null

    fun assetsAvailable(): Boolean = REQUIRED_FILES.all { assetExists("$MODEL_ID/$it") }
    fun lastFailure(): String? = lastFailure
    fun lastMetrics(): Metrics? = lastMetrics

    fun prepare(): Boolean = try {
        ensureEngine()
        true
    } catch (error: Throwable) {
        fail("engine candidata: ${describe(error)}", error)
        false
    }

    fun play(
        text: String,
        expression: AmoraVoiceExpression,
        volume: Float,
        stopRequested: AtomicBoolean,
        onStarted: () -> Unit,
    ): PlayResult {
        val cleanText = text.trim()
        if (cleanText.isEmpty()) return PlayResult.FAILED
        if (!assetsAvailable()) {
            fail("assets Supertonic/Amora ausentes no APK")
            return PlayResult.UNAVAILABLE
        }

        val localEngine = try {
            ensureEngine()
        } catch (error: Throwable) {
            fail("engine candidata: ${describe(error)}", error)
            return PlayResult.FAILED
        }

        publish(VoiceDiagnostics.STAGE_SYNTHESIZING, "Supertonic 60/40 • ${expression.label}")
        val synthesisStarted = System.nanoTime()
        val audio = try {
            localEngine.generateWithConfig(
                text = cleanText,
                config = GenerationConfig(
                    silenceScale = expression.pauseScale,
                    speed = expression.rateScale,
                    sid = 0,
                    numSteps = NUM_STEPS,
                    extra = mapOf("lang" to "pt", "seed" to "7"),
                ),
            )
        } catch (error: Throwable) {
            fail("Supertonic: ${describe(error)}", error)
            return PlayResult.FAILED
        }
        val synthesisSeconds = (System.nanoTime() - synthesisStarted) / 1_000_000_000.0
        if (stopRequested.get()) return PlayResult.INTERRUPTED
        if (audio.samples.isEmpty() || audio.sampleRate <= 0) {
            fail("Supertonic retornou áudio vazio")
            return PlayResult.FAILED
        }

        publish(VoiceDiagnostics.STAGE_SYNTHESIZING, "Aplicando FINAL_B + Signature V1 • ${expression.label}")
        val nativeStarted = System.nanoTime()
        val processed = try {
            AmoraVoiceNative.process(
                samples = audio.samples,
                sampleRate = audio.sampleRate,
                pitchRangeScale = expression.pitchRangeScale,
                f0RegisterScale = expression.f0RegisterScale,
                energyScale = expression.energyScale,
                softness = expression.softness,
                firmness = expression.firmness,
            )
        } catch (error: Throwable) {
            fail("WORLD JNI: ${describe(error)}", error)
            return PlayResult.FAILED
        }
        val nativeSeconds = (System.nanoTime() - nativeStarted) / 1_000_000_000.0
        if (stopRequested.get()) return PlayResult.INTERRUPTED
        if (processed.isEmpty()) {
            fail("WORLD JNI retornou áudio vazio; sem fallback oculto")
            return PlayResult.FAILED
        }

        val audioSeconds = processed.size.toDouble() / audio.sampleRate.toDouble()
        lastMetrics = Metrics(expression.label, synthesisSeconds, nativeSeconds, audioSeconds)
        val metrics = lastMetrics!!
        Log.i(
            TAG,
            "candidate emotion=%s synth=%.0fms native=%.0fms audio=%.2fs rtf=%.3f+%.3f=%.3f".format(
                metrics.expressionLabel,
                synthesisSeconds * 1000.0,
                nativeSeconds * 1000.0,
                audioSeconds,
                metrics.synthesisRtf,
                metrics.nativeRtf,
                metrics.totalRtf,
            ),
        )

        var track: AudioTrack? = null
        return try {
            track = createTrack(audio.sampleRate, volume)
            activeTrack = track
            track.play()
            publish(
                VoiceDiagnostics.STAGE_PLAYING,
                "FINAL_B + Signature V1 + Emotion V2 • ${expression.label} • RTF ${"%.3f".format(metrics.totalRtf)}",
            )
            onStarted()
            val written = writeSamples(track, processed, stopRequested)
            if (stopRequested.get()) PlayResult.INTERRUPTED
            else if (written != processed.size) {
                fail("AudioTrack escreveu $written de ${processed.size} amostras")
                PlayResult.FAILED
            } else {
                waitForPlayback(track, written, audio.sampleRate, stopRequested)
                if (stopRequested.get()) PlayResult.INTERRUPTED
                else {
                    lastFailure = null
                    publish(
                        VoiceDiagnostics.STAGE_READY,
                        "CANDIDATA AMORA OK • ${expression.label} • Galaxy A15 gate pendente",
                    )
                    PlayResult.COMPLETED
                }
            }
        } catch (error: Throwable) {
            if (stopRequested.get()) PlayResult.INTERRUPTED else {
                fail("reprodução candidata: ${describe(error)}", error)
                PlayResult.FAILED
            }
        } finally {
            if (activeTrack === track) activeTrack = null
            track?.let { local ->
                runCatching { local.stop() }
                runCatching { local.release() }
            }
        }
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
            require(assetsAvailable()) { "pacote Supertonic/Amora incompleto" }
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
                        voiceStyle = "$root/$AMORA_VOICE_BIN",
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
                require(created.numSpeakers() == 1) { "amora_voice.bin deveria ter 1 speaker; veio ${created.numSpeakers()}" }
                engine = created
                lastFailure = null
                publish(VoiceDiagnostics.STAGE_ENGINE_READY, "Supertonic custom 60/40 pronto • ${created.sampleRate()} Hz")
            }
        }
    }

    private fun createTrack(sampleRate: Int, volume: Float): AudioTrack {
        val minBuffer = AudioTrack.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
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
        ).also { track ->
            require(track.state == AudioTrack.STATE_INITIALIZED) { "AudioTrack não inicializou" }
            track.setVolume(volume.coerceIn(0f, 1f))
        }
    }

    private fun writeSamples(track: AudioTrack, source: FloatArray, stopRequested: AtomicBoolean): Int {
        val buffer = ShortArray(PCM_CHUNK_SAMPLES)
        var sourceOffset = 0
        while (sourceOffset < source.size && !stopRequested.get()) {
            val count = minOf(buffer.size, source.size - sourceOffset)
            for (index in 0 until count) {
                buffer[index] = (source[sourceOffset + index].coerceIn(-1f, 1f) * Short.MAX_VALUE).roundToInt().toShort()
            }
            var writtenOffset = 0
            while (writtenOffset < count && !stopRequested.get()) {
                val written = track.write(buffer, writtenOffset, count - writtenOffset, AudioTrack.WRITE_BLOCKING)
                if (written <= 0) return sourceOffset + writtenOffset
                writtenOffset += written
            }
            sourceOffset += writtenOffset
        }
        return sourceOffset
    }

    private fun waitForPlayback(track: AudioTrack, framesWritten: Int, sampleRate: Int, stopRequested: AtomicBoolean) {
        val audioMillis = framesWritten * 1000L / sampleRate.coerceAtLeast(1)
        val deadline = System.nanoTime() + (audioMillis + PLAYBACK_GRACE_MS) * 1_000_000L
        while (!stopRequested.get() && System.nanoTime() < deadline) {
            if (track.playbackHeadPosition >= framesWritten) return
            try { Thread.sleep(8) } catch (_: InterruptedException) {
                Thread.currentThread().interrupt()
                return
            }
        }
    }

    private fun assetExists(path: String): Boolean = runCatching {
        appContext.assets.open(path).use { true }
    }.getOrDefault(false)

    private fun publish(stage: String, detail: String) {
        diagnostics.record(stage, detail)
        onStatusChanged(diagnostics.snapshot())
    }

    private fun fail(detail: String, error: Throwable? = null) {
        lastFailure = detail.take(320)
        diagnostics.record(VoiceDiagnostics.STAGE_FAILED, lastFailure ?: "falha desconhecida")
        onStatusChanged(diagnostics.snapshot())
        if (error != null) Log.e(TAG, detail, error) else Log.e(TAG, detail)
    }

    private fun describe(error: Throwable): String = error.message?.takeIf { it.isNotBlank() }
        ?.let { "${error.javaClass.simpleName}: $it" }
        ?: error.javaClass.simpleName

    override fun close() {
        stop()
        synchronized(engineLock) {
            runCatching { engine?.release() }
            engine = null
        }
    }

    companion object {
        const val MODEL_ID = "sherpa-onnx-supertonic-3-tts-int8-2026-05-11"
        const val AMORA_VOICE_BIN = "amora_voice.bin"
        private const val NUM_STEPS = 8
        private const val TAG = "AmoraVoiceCandidate"
        private const val PCM_CHUNK_SAMPLES = 4_096
        private const val PLAYBACK_GRACE_MS = 1_500L
        private val REQUIRED_FILES = listOf(
            "duration_predictor.int8.onnx",
            "text_encoder.int8.onnx",
            "vector_estimator.int8.onnx",
            "vocoder.int8.onnx",
            "tts.json",
            "unicode_indexer.bin",
            AMORA_VOICE_BIN,
        )
    }
}

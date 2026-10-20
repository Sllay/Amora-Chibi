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
 * Rendering and playback are deliberately separate. The Android delivery layer can
 * prepare segment N+1 while segment N is already playing, removing the multi-second
 * silence that existed when every segment was rendered only after the previous one
 * had fully finished.
 *
 * IMPORTANT: do not use sherpa-onnx's Kotlin/JNI generation callback on Android in
 * this pinned build. Physical testing exposed a native process abort while the
 * callback path was active. We therefore use generateWithConfig() and check the
 * cancellation flag before/after the native generation boundary. Barge-in still
 * stops AudioTrack immediately and prevents completed-but-cancelled synthesis from
 * ever reaching playback; the currently-running native synthesis itself is allowed
 * to unwind safely instead of trying to cancel through JNI.
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

    data class PreparedAudio(
        val samples: FloatArray,
        val sampleRate: Int,
        val volume: Float,
        val metrics: Metrics,
    )

    sealed class PrepareResult {
        data class Ready(val audio: PreparedAudio) : PrepareResult()
        object Interrupted : PrepareResult()
        object Unavailable : PrepareResult()
        object Failed : PrepareResult()
    }

    private val appContext = context.applicationContext
    private val engineLock = Any()
    private val generationLock = Any()
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

    fun prepareAudio(
        text: String,
        expression: AmoraVoiceExpression,
        volume: Float,
        stopRequested: AtomicBoolean,
    ): PrepareResult {
        val cleanText = text.trim()
        if (cleanText.isEmpty()) return PrepareResult.Failed
        if (!assetsAvailable()) {
            fail("assets Supertonic/Amora ausentes no APK")
            return PrepareResult.Unavailable
        }
        if (stopRequested.get()) return PrepareResult.Interrupted

        val localEngine = try {
            ensureEngine()
        } catch (error: Throwable) {
            fail("engine candidata: ${describe(error)}", error)
            return PrepareResult.Failed
        }

        return synchronized(generationLock) {
            if (stopRequested.get()) return@synchronized PrepareResult.Interrupted
            publish(VoiceDiagnostics.STAGE_SYNTHESIZING, "Supertonic 60/40 • ${expression.label}")
            val synthesisStarted = System.nanoTime()
            val audio = try {
                // Android safety invariant: no Kotlin/JNI generation callback here.
                // generateWithConfigAndCallback() caused a native process abort in
                // the physical dev12 gate. Cancellation is observed immediately
                // before and after this native boundary instead.
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
                if (stopRequested.get()) return@synchronized PrepareResult.Interrupted
                fail("Supertonic: ${describe(error)}", error)
                return@synchronized PrepareResult.Failed
            }
            val synthesisSeconds = (System.nanoTime() - synthesisStarted) / 1_000_000_000.0
            if (stopRequested.get()) return@synchronized PrepareResult.Interrupted
            if (audio.samples.isEmpty() || audio.sampleRate <= 0) {
                fail("Supertonic retornou áudio vazio")
                return@synchronized PrepareResult.Failed
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
                if (stopRequested.get()) return@synchronized PrepareResult.Interrupted
                fail("WORLD JNI: ${describe(error)}", error)
                return@synchronized PrepareResult.Failed
            }
            val nativeSeconds = (System.nanoTime() - nativeStarted) / 1_000_000_000.0
            if (stopRequested.get()) return@synchronized PrepareResult.Interrupted
            if (processed.isEmpty()) {
                fail("WORLD JNI retornou áudio vazio; sem fallback oculto")
                return@synchronized PrepareResult.Failed
            }

            val audioSeconds = processed.size.toDouble() / audio.sampleRate.toDouble()
            val metrics = Metrics(expression.label, synthesisSeconds, nativeSeconds, audioSeconds)
            lastMetrics = metrics
            Log.i(
                TAG,
                "candidate emotion=%s synth=%.0fms native=%.0fms audio=%.2fs rtf=%.3f+%.3f=%.3f steps=%d".format(
                    metrics.expressionLabel,
                    synthesisSeconds * 1000.0,
                    nativeSeconds * 1000.0,
                    audioSeconds,
                    metrics.synthesisRtf,
                    metrics.nativeRtf,
                    metrics.totalRtf,
                    NUM_STEPS,
                ),
            )
            publish(
                VoiceDiagnostics.STAGE_READY,
                "segmento pronto • ${expression.label} • RTF ${"%.3f".format(metrics.totalRtf)} • $NUM_STEPS steps",
            )
            PrepareResult.Ready(
                PreparedAudio(
                    samples = processed,
                    sampleRate = audio.sampleRate,
                    volume = volume.coerceIn(0f, 1f),
                    metrics = metrics,
                )
            )
        }
    }

    fun playPrepared(
        prepared: PreparedAudio,
        stopRequested: AtomicBoolean,
        onStarted: () -> Unit,
    ): PlayResult {
        if (stopRequested.get()) return PlayResult.INTERRUPTED
        val metrics = prepared.metrics
        var track: AudioTrack? = null
        return try {
            track = createTrack(prepared.sampleRate, prepared.volume)
            activeTrack = track
            track.play()
            publish(
                VoiceDiagnostics.STAGE_PLAYING,
                "FINAL_B + Signature V1 + Emotion V2 • ${metrics.expressionLabel} • RTF ${"%.3f".format(metrics.totalRtf)}",
            )
            onStarted()
            val written = writeSamples(track, prepared.samples, stopRequested)
            if (stopRequested.get()) PlayResult.INTERRUPTED
            else if (written != prepared.samples.size) {
                fail("AudioTrack escreveu $written de ${prepared.samples.size} amostras")
                PlayResult.FAILED
            } else {
                waitForPlayback(track, written, prepared.sampleRate, stopRequested)
                if (stopRequested.get()) PlayResult.INTERRUPTED
                else {
                    lastFailure = null
                    publish(
                        VoiceDiagnostics.STAGE_READY,
                        "CANDIDATA AMORA OK • ${metrics.expressionLabel} • Galaxy A15 gate pendente",
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

    fun play(
        text: String,
        expression: AmoraVoiceExpression,
        volume: Float,
        stopRequested: AtomicBoolean,
        onStarted: () -> Unit,
    ): PlayResult = when (val prepared = prepareAudio(text, expression, volume, stopRequested)) {
        is PrepareResult.Ready -> playPrepared(prepared.audio, stopRequested, onStarted)
        PrepareResult.Interrupted -> PlayResult.INTERRUPTED
        PrepareResult.Unavailable -> PlayResult.UNAVAILABLE
        PrepareResult.Failed -> PlayResult.FAILED
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
        // Supertonic documents 5-12 denoising steps as a normal quality/speed range.
        // dev13 was 8 steps and measured above real time on the Galaxy A15. Five is
        // the first physical candidate intended to bring synth+WORLD close to/below
        // RTF 1.0 without changing FINAL_B, VoiceSignature or speaker identity.
        private const val NUM_STEPS = 5
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

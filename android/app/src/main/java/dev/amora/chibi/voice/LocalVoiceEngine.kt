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
 * Canonical local/offline Amora speech path.
 *
 * Supertonic speaker 0 produces the base PCM selected physically by BK. A
 * validated VoiceProfile from the local Core then controls bounded prosody and
 * deterministic VoiceSignature DSP. No profile update can swap the base speaker.
 */
class LocalVoiceEngine(
    context: Context,
    private val identity: VoiceIdentity = VoiceIdentity.AMORA_V1,
    private val diagnostics: VoiceDiagnostics = VoiceDiagnostics(context),
    private val onStatusChanged: (VoiceDiagnostics.Snapshot) -> Unit = {},
) : AutoCloseable {
    enum class PlayResult { COMPLETED, UNAVAILABLE, FAILED, INTERRUPTED }

    private val appContext = context.applicationContext
    private val engineLock = Any()
    @Volatile private var engine: OfflineTts? = null
    @Volatile private var activeTrack: AudioTrack? = null
    @Volatile private var lastFailure: String? = null

    fun assetsAvailable(): Boolean = identity.requiredFiles.all { assetExists("${identity.packId}/$it") }

    fun lastFailure(): String? = lastFailure

    fun play(
        text: String,
        requestedRate: Float,
        requestedPitch: Float,
        valence: Float,
        arousal: Float,
        volume: Float,
        profile: VoiceProfileConfig,
        stopRequested: AtomicBoolean,
        onStarted: () -> Unit,
    ): PlayResult {
        if (!assetsAvailable()) {
            fail("assets Supertonic da voz canônica ausentes no APK")
            return PlayResult.UNAVAILABLE
        }
        val validatedProfile = try {
            profile.validateFor(identity)
        } catch (error: Throwable) {
            fail("voice profile inválido: ${describe(error)}", error)
            return PlayResult.FAILED
        }
        val effective = validatedProfile.effective(
            requestedRate = requestedRate,
            requestedPitch = requestedPitch,
            valence = valence,
            arousal = arousal,
        )
        publish(
            VoiceDiagnostics.STAGE_ASSETS_READY,
            "Supertonic speaker ${identity.speakerId} · ${validatedProfile.activeProfile} r${validatedProfile.revision}",
        )

        val localEngine = try {
            ensureEngine()
        } catch (error: Throwable) {
            fail("engine: ${describe(error)}", error)
            return PlayResult.FAILED
        }

        publish(VoiceDiagnostics.STAGE_SYNTHESIZING, "gerando PCM base local + VoiceSignature")
        val startedNs = System.nanoTime()
        val audio = try {
            localEngine.generateWithConfig(
                text = text,
                config = GenerationConfig(
                    silenceScale = effective.silenceScale,
                    speed = effective.speed,
                    sid = identity.speakerId,
                    numSteps = effective.numSteps,
                    extra = mapOf("lang" to identity.language),
                ),
            )
        } catch (error: Throwable) {
            fail("síntese: ${describe(error)}", error)
            return PlayResult.FAILED
        }
        if (stopRequested.get()) return PlayResult.INTERRUPTED
        if (audio.samples.isEmpty() || audio.sampleRate <= 0) {
            fail("síntese retornou áudio vazio")
            return PlayResult.FAILED
        }

        val processed = try {
            VoiceSignatureProcessor.process(audio.samples, audio.sampleRate, effective)
        } catch (error: Throwable) {
            fail("VoiceSignature: ${describe(error)}", error)
            return PlayResult.FAILED
        }
        if (stopRequested.get()) return PlayResult.INTERRUPTED

        val synthesisSeconds = (System.nanoTime() - startedNs) / 1_000_000_000.0
        val audioSeconds = processed.size.toDouble() / audio.sampleRate.toDouble()
        val rtf = if (audioSeconds > 0.0) synthesisSeconds / audioSeconds else 0.0
        Log.i(
            TAG,
            "Amora voice sintetizada: %.0fms áudio=%.2fs rtf=%.3f speaker=%d profile=%s r%d".format(
                synthesisSeconds * 1000.0,
                audioSeconds,
                rtf,
                identity.speakerId,
                validatedProfile.activeProfile,
                validatedProfile.revision,
            ),
        )

        var track: AudioTrack? = null
        return try {
            track = createTrack(audio.sampleRate, volume)
            activeTrack = track
            track.play()
            publish(
                VoiceDiagnostics.STAGE_PLAYING,
                "AMORA_CANONICAL ${audio.sampleRate} Hz · ${validatedProfile.activeProfile} r${validatedProfile.revision}",
            )
            onStarted()
            val framesWritten = writeSamples(track, processed, stopRequested)
            if (stopRequested.get()) {
                PlayResult.INTERRUPTED
            } else if (framesWritten != processed.size) {
                fail("AudioTrack escreveu $framesWritten de ${processed.size} amostras")
                PlayResult.FAILED
            } else {
                waitForPlayback(track, framesWritten, audio.sampleRate, stopRequested)
                if (stopRequested.get()) {
                    PlayResult.INTERRUPTED
                } else {
                    lastFailure = null
                    publish(
                        VoiceDiagnostics.STAGE_READY,
                        "Amora Voice pronta · speaker ${identity.speakerId} · ${validatedProfile.activeProfile} r${validatedProfile.revision}",
                    )
                    PlayResult.COMPLETED
                }
            }
        } catch (error: Throwable) {
            if (stopRequested.get()) PlayResult.INTERRUPTED
            else {
                fail("reprodução: ${describe(error)}", error)
                PlayResult.FAILED
            }
        } finally {
            activeTrack = null
            track?.let { localTrack ->
                runCatching { localTrack.stop() }
                runCatching { localTrack.release() }
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
            require(assetsAvailable()) { "pacote ${identity.packId} incompleto" }
            val root = identity.packId
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
                    numThreads = identity.numThreads,
                    debug = false,
                    provider = "cpu",
                ),
                maxNumSentences = 1,
                silenceScale = 1.0f,
            )
            return OfflineTts(assetManager = appContext.assets, config = config).also { created ->
                require(created.sampleRate() > 0) { "sherpa retornou sampleRate inválido" }
                require(created.numSpeakers() > identity.speakerId) {
                    "speaker ${identity.speakerId} indisponível; modelo expõe ${created.numSpeakers()}"
                }
                engine = created
                lastFailure = null
                publish(
                    VoiceDiagnostics.STAGE_ENGINE_READY,
                    "Supertonic pronto · speaker ${identity.speakerId} · ${created.sampleRate()} Hz",
                )
                Log.i(TAG, "LocalVoiceEngine pronto: ${identity.id} sampleRate=${created.sampleRate()}")
            }
        }
    }

    private fun assetExists(path: String): Boolean = runCatching {
        appContext.assets.open(path).use { true }
    }.getOrDefault(false)

    private fun createTrack(sampleRate: Int, volume: Float): AudioTrack {
        val minBuffer = AudioTrack.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        require(minBuffer > 0) { "AudioTrack não suporta sampleRate=$sampleRate" }
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
                buffer[index] = (source[sourceOffset + index].coerceIn(-1f, 1f) * Short.MAX_VALUE)
                    .roundToInt().toShort()
            }
            var writtenOffset = 0
            while (writtenOffset < count && !stopRequested.get()) {
                val written = track.write(buffer, writtenOffset, count - writtenOffset, AudioTrack.WRITE_BLOCKING)
                if (written <= 0) return sourceOffset + writtenOffset
                writtenOffset += written
            }
            sourceOffset += writtenOffset
            if (writtenOffset != count) break
        }
        return sourceOffset
    }

    private fun waitForPlayback(track: AudioTrack, framesWritten: Int, sampleRate: Int, stopRequested: AtomicBoolean) {
        if (framesWritten <= 0) return
        val audioMillis = framesWritten * 1000L / sampleRate.coerceAtLeast(1)
        val deadline = System.nanoTime() + (audioMillis + PLAYBACK_GRACE_MS) * 1_000_000L
        while (!stopRequested.get() && System.nanoTime() < deadline) {
            if (track.playbackHeadPosition >= framesWritten) return
            try {
                Thread.sleep(8)
            } catch (_: InterruptedException) {
                Thread.currentThread().interrupt()
                return
            }
        }
    }

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

    private fun describe(error: Throwable): String {
        val message = error.message?.takeIf { it.isNotBlank() }
        return if (message == null) error.javaClass.simpleName else "${error.javaClass.simpleName}: $message"
    }

    override fun close() {
        stop()
        synchronized(engineLock) {
            runCatching { engine?.release() }
            engine = null
        }
    }

    companion object {
        private const val TAG = "AmoraLocalVoice"
        private const val PCM_CHUNK_SAMPLES = 4_096
        private const val PLAYBACK_GRACE_MS = 1_500L
    }
}

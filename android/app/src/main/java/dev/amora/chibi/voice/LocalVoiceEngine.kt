package dev.amora.chibi.voice

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.util.Log
import com.k2fsa.sherpa.onnx.OfflineTts
import com.k2fsa.sherpa.onnx.OfflineTtsConfig
import com.k2fsa.sherpa.onnx.OfflineTtsModelConfig
import com.k2fsa.sherpa.onnx.OfflineTtsVitsModelConfig
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.roundToInt

/**
 * Neural, quota-free speech synthesis for Amora.
 *
 * Each Core speech segment is already short, so V1 deliberately uses the simple
 * synchronous sherpa generate() path instead of a JNI streaming callback. This
 * keeps the mobile failure surface small while still allowing immediate playback
 * once one segment has been synthesized.
 */
class LocalVoiceEngine(
    private val packManager: VoicePackManager,
    private val identity: VoiceIdentity = VoiceIdentity.AMORA_V1_PROVISIONAL,
) : AutoCloseable {
    enum class PlayResult { COMPLETED, UNAVAILABLE, FAILED, INTERRUPTED }

    private val engineLock = Any()
    @Volatile private var engine: OfflineTts? = null
    @Volatile private var activeTrack: AudioTrack? = null

    fun isReady(): Boolean = packManager.readyRoot() != null

    fun play(
        text: String,
        requestedRate: Float,
        volume: Float,
        stopRequested: AtomicBoolean,
        onStarted: () -> Unit,
    ): PlayResult {
        val root = packManager.readyRoot() ?: return PlayResult.UNAVAILABLE
        val localEngine = try {
            ensureEngine(root)
        } catch (error: Throwable) {
            Log.e(TAG, "Não foi possível iniciar o TTS neural local", error)
            return PlayResult.FAILED
        }

        val startedNs = System.nanoTime()
        val audio = try {
            localEngine.generate(
                text = text,
                sid = 0,
                speed = identity.speedFor(requestedRate),
            )
        } catch (error: Throwable) {
            Log.e(TAG, "Falha ao sintetizar voz local", error)
            return PlayResult.FAILED
        }
        if (stopRequested.get()) return PlayResult.INTERRUPTED
        if (audio.samples.isEmpty() || audio.sampleRate <= 0) {
            Log.w(TAG, "TTS neural retornou áudio vazio")
            return PlayResult.FAILED
        }

        val synthesisSeconds = (System.nanoTime() - startedNs) / 1_000_000_000.0
        val audioSeconds = audio.samples.size.toDouble() / audio.sampleRate.toDouble()
        val rtf = if (audioSeconds > 0.0) synthesisSeconds / audioSeconds else 0.0
        Log.i(
            TAG,
            "Voz local sintetizada: %.0fms áudio=%.2fs rtf=%.3f modelo=%s".format(
                synthesisSeconds * 1000.0,
                audioSeconds,
                rtf,
                identity.id,
            ),
        )

        var track: AudioTrack? = null
        return try {
            track = createTrack(audio.sampleRate, volume)
            activeTrack = track
            track.play()
            onStarted()
            val framesWritten = writeSamples(track, audio.samples, stopRequested)
            if (stopRequested.get()) {
                PlayResult.INTERRUPTED
            } else if (framesWritten != audio.samples.size) {
                PlayResult.FAILED
            } else {
                waitForPlayback(track, framesWritten, audio.sampleRate, stopRequested)
                if (stopRequested.get()) PlayResult.INTERRUPTED else PlayResult.COMPLETED
            }
        } catch (error: Throwable) {
            if (stopRequested.get()) PlayResult.INTERRUPTED
            else {
                Log.e(TAG, "Falha ao reproduzir voz neural local", error)
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

    private fun ensureEngine(root: File): OfflineTts {
        engine?.let { return it }
        synchronized(engineLock) {
            engine?.let { return it }
            val model = File(root, identity.modelFileName)
            val tokens = File(root, identity.tokensFileName)
            val data = File(root, identity.dataDirectoryName)
            require(model.isFile && tokens.isFile && data.isDirectory) {
                "pacote de voz incompleto"
            }
            val config = OfflineTtsConfig(
                model = OfflineTtsModelConfig(
                    vits = OfflineTtsVitsModelConfig(
                        model = model.absolutePath,
                        tokens = tokens.absolutePath,
                        dataDir = data.absolutePath,
                    ),
                    numThreads = identity.numThreads,
                    debug = false,
                    provider = "cpu",
                ),
                maxNumSentences = 1,
                silenceScale = 0.2f,
            )
            return OfflineTts(config = config).also {
                engine = it
                Log.i(TAG, "LocalVoiceEngine pronto: ${identity.id} sampleRate=${it.sampleRate()}")
            }
        }
    }

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
        ).also { it.setVolume(volume.coerceIn(0f, 1f)) }
    }

    private fun writeSamples(
        track: AudioTrack,
        source: FloatArray,
        stopRequested: AtomicBoolean,
    ): Int {
        val buffer = ShortArray(PCM_CHUNK_SAMPLES)
        var sourceOffset = 0
        while (sourceOffset < source.size && !stopRequested.get()) {
            val count = minOf(buffer.size, source.size - sourceOffset)
            for (index in 0 until count) {
                val scaled = source[sourceOffset + index] * identity.outputGain
                buffer[index] = (scaled.coerceIn(-1f, 1f) * Short.MAX_VALUE).roundToInt().toShort()
            }
            var writtenOffset = 0
            while (writtenOffset < count && !stopRequested.get()) {
                val written = track.write(
                    buffer,
                    writtenOffset,
                    count - writtenOffset,
                    AudioTrack.WRITE_BLOCKING,
                )
                if (written <= 0) {
                    Log.w(TAG, "AudioTrack recusou PCM local: $written")
                    return sourceOffset + writtenOffset
                }
                writtenOffset += written
            }
            sourceOffset += writtenOffset
            if (writtenOffset != count) break
        }
        return sourceOffset
    }

    private fun waitForPlayback(
        track: AudioTrack,
        framesWritten: Int,
        sampleRate: Int,
        stopRequested: AtomicBoolean,
    ) {
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

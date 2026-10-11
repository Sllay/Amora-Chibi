package dev.amora.chibi.voice

import android.content.Context
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
import java.io.FileOutputStream
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.roundToInt

/**
 * Neural, quota-free speech synthesis for Amora.
 *
 * The provisional V1 voice is packaged in APK assets at build time, matching the
 * sherpa-onnx Android reference path that was physically proven on Galaxy A15.
 * Model/tokens remain assets; espeak-ng-data is copied to a real app-private
 * directory because the native espeak runtime requires a filesystem dataDir.
 */
class LocalVoiceEngine(
    context: Context,
    private val identity: VoiceIdentity = VoiceIdentity.AMORA_V1_PROVISIONAL,
    private val diagnostics: VoiceDiagnostics = VoiceDiagnostics(context),
    private val onStatusChanged: (VoiceDiagnostics.Snapshot) -> Unit = {},
) : AutoCloseable {
    enum class PlayResult { COMPLETED, UNAVAILABLE, FAILED, INTERRUPTED }

    private val appContext = context.applicationContext
    private val engineLock = Any()
    @Volatile private var engine: OfflineTts? = null
    @Volatile private var activeTrack: AudioTrack? = null
    @Volatile private var lastFailure: String? = null

    fun assetsAvailable(): Boolean {
        val model = assetExists("${identity.packId}/${identity.modelFileName}")
        val tokens = assetExists("${identity.packId}/${identity.tokensFileName}")
        val data = runCatching {
            appContext.assets.list("${identity.packId}/${identity.dataDirectoryName}")?.isNotEmpty() == true
        }.getOrDefault(false)
        return model && tokens && data
    }

    fun lastFailure(): String? = lastFailure

    fun play(
        text: String,
        requestedRate: Float,
        volume: Float,
        stopRequested: AtomicBoolean,
        onStarted: () -> Unit,
    ): PlayResult {
        if (!assetsAvailable()) {
            fail("assets da voz provisória ausentes no APK")
            return PlayResult.UNAVAILABLE
        }
        publish(VoiceDiagnostics.STAGE_ASSETS_READY, "modelo, tokens e espeak empacotados")

        val localEngine = try {
            ensureEngine()
        } catch (error: Throwable) {
            fail("engine: ${describe(error)}", error)
            return PlayResult.FAILED
        }

        publish(VoiceDiagnostics.STAGE_SYNTHESIZING, "gerando PCM local")
        val startedNs = System.nanoTime()
        val audio = try {
            localEngine.generate(
                text = text,
                sid = 0,
                speed = identity.speedFor(requestedRate),
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
            publish(VoiceDiagnostics.STAGE_PLAYING, "PCM ${audio.sampleRate} Hz via AudioTrack")
            onStarted()
            val framesWritten = writeSamples(track, audio.samples, stopRequested)
            if (stopRequested.get()) {
                PlayResult.INTERRUPTED
            } else if (framesWritten != audio.samples.size) {
                fail("AudioTrack escreveu $framesWritten de ${audio.samples.size} amostras")
                PlayResult.FAILED
            } else {
                waitForPlayback(track, framesWritten, audio.sampleRate, stopRequested)
                if (stopRequested.get()) {
                    PlayResult.INTERRUPTED
                } else {
                    lastFailure = null
                    publish(VoiceDiagnostics.STAGE_READY, "Faber local sintetizou e reproduziu com sucesso")
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
            publish(VoiceDiagnostics.STAGE_PREPARING_DATA, "copiando espeak-ng-data para storage privado")
            val dataDir = prepareEspeakData()
            val assetRoot = identity.packId
            val config = OfflineTtsConfig(
                model = OfflineTtsModelConfig(
                    vits = OfflineTtsVitsModelConfig(
                        model = "$assetRoot/${identity.modelFileName}",
                        tokens = "$assetRoot/${identity.tokensFileName}",
                        dataDir = dataDir.absolutePath,
                    ),
                    numThreads = identity.numThreads,
                    debug = false,
                    provider = "cpu",
                ),
                maxNumSentences = 1,
                silenceScale = 0.2f,
            )
            return OfflineTts(assetManager = appContext.assets, config = config).also {
                val sampleRate = it.sampleRate()
                require(sampleRate > 0) { "sherpa retornou sampleRate inválido=$sampleRate" }
                engine = it
                lastFailure = null
                publish(VoiceDiagnostics.STAGE_ENGINE_READY, "OfflineTts pronto · ${sampleRate} Hz")
                Log.i(TAG, "LocalVoiceEngine pronto: ${identity.id} sampleRate=$sampleRate")
            }
        }
    }

    private fun prepareEspeakData(): File {
        val runtimeRoot = File(appContext.filesDir, "voice_runtime/${identity.packId}")
        val dataDir = File(runtimeRoot, identity.dataDirectoryName)
        val marker = File(runtimeRoot, ".espeak-${identity.expectedModelSha256.take(16)}")
        if (marker.isFile && dataDir.isDirectory && dataDir.list()?.isNotEmpty() == true) return dataDir

        runtimeRoot.deleteRecursively()
        runtimeRoot.mkdirs()
        val assetPath = "${identity.packId}/${identity.dataDirectoryName}"
        copyAssetTree(assetPath, dataDir)
        require(dataDir.isDirectory && dataDir.list()?.isNotEmpty() == true) { "espeak-ng-data não foi copiado" }
        marker.writeText(identity.expectedModelSha256, Charsets.UTF_8)
        return dataDir
    }

    private fun copyAssetTree(assetPath: String, destination: File) {
        val children = appContext.assets.list(assetPath) ?: emptyArray()
        if (children.isEmpty()) {
            destination.parentFile?.mkdirs()
            appContext.assets.open(assetPath).use { input ->
                FileOutputStream(destination).use { output -> input.copyTo(output, COPY_BUFFER_BYTES) }
            }
            return
        }
        destination.mkdirs()
        for (child in children) {
            copyAssetTree("$assetPath/$child", File(destination, child))
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
                val scaled = source[sourceOffset + index] * identity.outputGain
                buffer[index] = (scaled.coerceIn(-1f, 1f) * Short.MAX_VALUE).roundToInt().toShort()
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
        private const val COPY_BUFFER_BYTES = 64 * 1024
        private const val PCM_CHUNK_SAMPLES = 4_096
        private const val PLAYBACK_GRACE_MS = 1_500L
    }
}

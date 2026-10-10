package dev.amora.chibi.voice

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import dev.amora.chibi.core.CoreClient
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

class VoiceOutput(
    context: Context,
    private val onSpeakingChanged: (Boolean) -> Unit,
    private val onFinished: (String, Outcome, Int?) -> Unit,
) : TextToSpeech.OnInitListener, AutoCloseable {
    enum class Outcome { SPOKEN, FAILED, INTERRUPTED, PARTIAL }

    private val ready = AtomicBoolean(false)
    private val stopRequested = AtomicBoolean(false)
    private val current = AtomicReference<CoreClient.SpeechUtterance?>(null)
    private val localProgressKnown = AtomicBoolean(false)
    private val lastSpokenEnd = AtomicInteger(0)
    private val tts = TextToSpeech(context.applicationContext, this)
    private val coreClient = CoreClient(context.applicationContext)
    @Volatile private var activeTrack: AudioTrack? = null

    init {
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String) { onSpeakingChanged(true) }
            override fun onDone(utteranceId: String) { finishLocal(utteranceId, Outcome.SPOKEN) }
            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String) { finishLocal(utteranceId, Outcome.FAILED) }
            override fun onError(utteranceId: String, errorCode: Int) {
                Log.w(TAG, "TTS local falhou id=$utteranceId code=$errorCode")
                finishLocal(utteranceId, Outcome.FAILED)
            }
            override fun onStop(utteranceId: String, interrupted: Boolean) {
                finishLocal(utteranceId, if (interrupted) Outcome.INTERRUPTED else Outcome.FAILED)
            }
            override fun onRangeStart(utteranceId: String, start: Int, end: Int, frame: Int) {
                val item = current.get() ?: return
                if (item.id != utteranceId || !localProgressKnown.get()) return
                lastSpokenEnd.accumulateAndGet(end.coerceIn(0, item.text.length), ::maxOf)
            }
        })
    }

    override fun onInit(status: Int) {
        ready.set(status == TextToSpeech.SUCCESS)
        if (!ready.get()) Log.w(TAG, "Android TextToSpeech não inicializou: $status")
    }

    fun currentUtterance(): CoreClient.SpeechUtterance? = current.get()

    fun speak(item: CoreClient.SpeechUtterance): Boolean {
        if (!current.compareAndSet(null, item)) return false
        stopRequested.set(false)
        localProgressKnown.set(false)
        lastSpokenEnd.set(0)
        if (item.remoteTtsAllowed) {
            val remote = runCatching { playRemote(item) }
                .onFailure { Log.w(TAG, "TTS expressivo remoto falhou ${item.id}", it) }
                .getOrNull()
            when (remote) {
                RemoteResult.COMPLETED -> { finish(item, Outcome.SPOKEN); return true }
                RemoteResult.INTERRUPTED -> { finish(item, Outcome.INTERRUPTED); return true }
                RemoteResult.PARTIAL_FAILURE -> { finish(item, Outcome.PARTIAL); return true }
                RemoteResult.UNAVAILABLE, null -> Unit
            }
        }
        if (!ready.get()) {
            current.compareAndSet(item, null)
            return false
        }
        return startLocal(item)
    }

    fun stopCurrent(): CoreClient.SpeechUtterance? {
        val item = current.get() ?: return null
        stopRequested.set(true)
        runCatching { activeTrack?.pause() }
        runCatching { activeTrack?.flush() }
        runCatching { activeTrack?.stop() }
        runCatching { tts.stop() }
        finish(item, Outcome.INTERRUPTED)
        return item
    }

    private fun startLocal(item: CoreClient.SpeechUtterance): Boolean {
        localProgressKnown.set(true)
        lastSpokenEnd.set(0)
        val locale = Locale.forLanguageTag(item.locale)
        val languageResult = tts.setLanguage(locale)
        if (languageResult == TextToSpeech.LANG_MISSING_DATA || languageResult == TextToSpeech.LANG_NOT_SUPPORTED) {
            Log.w(TAG, "Voz/locale não suportado: ${item.locale}")
        }
        selectBestLocalVoice(locale)
        tts.setSpeechRate(item.rate.coerceIn(0.72F, 1.35F))
        tts.setPitch(item.pitch.coerceIn(0.80F, 1.28F))
        val params = Bundle().apply { putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, item.volume.coerceIn(0F, 1F)) }
        val result = tts.speak(item.text, TextToSpeech.QUEUE_FLUSH, params, item.id)
        if (result == TextToSpeech.SUCCESS) {
            onSpeakingChanged(true)
            return true
        }
        current.compareAndSet(item, null)
        localProgressKnown.set(false)
        return false
    }

    private fun selectBestLocalVoice(locale: Locale) {
        val voices = runCatching { tts.voices }.getOrNull().orEmpty()
        val languageMatches = voices.filter { it.locale.language == locale.language }
        val candidates = if (languageMatches.isNotEmpty()) languageMatches else voices.toList()
        val chosen = candidates.sortedWith(
            compareByDescending<android.speech.tts.Voice> { it.locale.country.equals(locale.country, ignoreCase = true) }
                .thenByDescending { !it.isNetworkConnectionRequired }
                .thenByDescending { it.quality }
                .thenBy { it.latency }
        ).firstOrNull()
        if (chosen != null) {
            runCatching { tts.voice = chosen }
                .onFailure { Log.w(TAG, "Não foi possível selecionar voz local ${chosen.name}", it) }
        }
    }

    private fun playRemote(item: CoreClient.SpeechUtterance): RemoteResult {
        localProgressKnown.set(false)
        var track: AudioTrack? = null
        var anyAudio = false
        try {
            val result = coreClient.streamVoiceAudio(item.id) { chunk ->
                if (stopRequested.get()) return@streamVoiceAudio false
                if (track == null) {
                    track = createTrack(item.volume).also {
                        activeTrack = it
                        it.play()
                        onSpeakingChanged(true)
                    }
                }
                anyAudio = true
                val written = track?.write(chunk, 0, chunk.size, AudioTrack.WRITE_BLOCKING) ?: -1
                written >= 0 && !stopRequested.get()
            }
            if (!result.usedRemote) return RemoteResult.UNAVAILABLE
            if (stopRequested.get()) return RemoteResult.INTERRUPTED
            if (!result.completed) return if (anyAudio) RemoteResult.PARTIAL_FAILURE else RemoteResult.UNAVAILABLE
            waitForPlayback(track, result.bytesReceived)
            return if (stopRequested.get()) RemoteResult.INTERRUPTED else RemoteResult.COMPLETED
        } finally {
            val localTrack = track
            activeTrack = null
            if (localTrack != null) {
                runCatching { localTrack.stop() }
                runCatching { localTrack.release() }
            }
        }
    }

    private fun createTrack(volume: Float): AudioTrack {
        val sampleRate = 24_000
        val minBuffer = AudioTrack.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val attributes = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ASSISTANT).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build()
        val format = AudioFormat.Builder().setSampleRate(sampleRate).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).setEncoding(AudioFormat.ENCODING_PCM_16BIT).build()
        return AudioTrack(attributes, format, maxOf(minBuffer, sampleRate), AudioTrack.MODE_STREAM, AudioManager.AUDIO_SESSION_ID_GENERATE)
            .also { it.setVolume(volume.coerceIn(0F, 1F)) }
    }

    private fun waitForPlayback(track: AudioTrack?, bytesReceived: Long) {
        if (track == null || bytesReceived <= 0) return
        val targetFrames = (bytesReceived / 2L).coerceAtMost(Int.MAX_VALUE.toLong()).toInt()
        val deadline = System.nanoTime() + 8_000_000_000L
        while (!stopRequested.get() && System.nanoTime() < deadline) {
            if (track.playbackHeadPosition >= targetFrames) return
            try { Thread.sleep(8) } catch (_: InterruptedException) { Thread.currentThread().interrupt(); return }
        }
    }

    private fun finishLocal(utteranceId: String, outcome: Outcome) {
        val item = current.get() ?: return
        if (item.id != utteranceId) return
        finish(item, outcome)
    }

    private fun finish(item: CoreClient.SpeechUtterance, outcome: Outcome) {
        if (!current.compareAndSet(item, null)) return
        val spokenChars = when (outcome) {
            Outcome.SPOKEN -> item.text.length
            Outcome.INTERRUPTED -> if (localProgressKnown.get()) lastSpokenEnd.get().coerceIn(0, item.text.length) else null
            Outcome.FAILED, Outcome.PARTIAL -> null
        }
        localProgressKnown.set(false)
        onSpeakingChanged(false)
        onFinished(item.id, outcome, spokenChars)
    }

    override fun close() {
        stopRequested.set(true)
        current.getAndSet(null)
        runCatching { activeTrack?.stop() }
        runCatching { activeTrack?.release() }
        activeTrack = null
        localProgressKnown.set(false)
        ready.set(false)
        runCatching { tts.stop() }
        runCatching { tts.shutdown() }
        onSpeakingChanged(false)
    }

    private enum class RemoteResult { COMPLETED, UNAVAILABLE, INTERRUPTED, PARTIAL_FAILURE }
    companion object { private const val TAG = "AmoraVoice" }
}

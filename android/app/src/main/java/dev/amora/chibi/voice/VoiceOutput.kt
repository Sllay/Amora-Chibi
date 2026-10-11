package dev.amora.chibi.voice

import android.content.Context
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import dev.amora.chibi.core.CoreClient
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

/**
 * Stable voice output for Amora.
 *
 * Normal speech is synthesized by the pinned local neural VoiceIdentity. Remote
 * Gemini TTS is deliberately not part of this path anymore: quota changes must
 * never change Amora's timbre. Android TextToSpeech remains emergency/bootstrap
 * fallback only while the local voice pack is downloading or unavailable.
 */
class VoiceOutput(
    context: Context,
    private val onSpeakingChanged: (Boolean) -> Unit,
    private val onFinished: (String, Outcome, Int?) -> Unit,
    private val onVoicePackChanged: (VoicePackManager.Snapshot) -> Unit = {},
) : TextToSpeech.OnInitListener, AutoCloseable {
    enum class Outcome { SPOKEN, FAILED, INTERRUPTED, PARTIAL }

    private val identity = VoiceIdentity.AMORA_V1_PROVISIONAL
    private val ready = AtomicBoolean(false)
    private val stopRequested = AtomicBoolean(false)
    private val current = AtomicReference<CoreClient.SpeechUtterance?>(null)
    private val androidProgressKnown = AtomicBoolean(false)
    private val lastSpokenEnd = AtomicInteger(0)
    private val androidInit = CountDownLatch(1)
    private val tts = TextToSpeech(context.applicationContext, this)
    private val packManager = VoicePackManager(context.applicationContext, identity)
    private val localVoice = LocalVoiceEngine(packManager, identity)
    @Volatile private var selectedFallbackVoiceName: String? = null

    init {
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String) { onSpeakingChanged(true) }
            override fun onDone(utteranceId: String) { finishAndroid(utteranceId, Outcome.SPOKEN) }
            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String) { finishAndroid(utteranceId, Outcome.FAILED) }
            override fun onError(utteranceId: String, errorCode: Int) {
                Log.w(TAG, "TTS Android de emergência falhou id=$utteranceId code=$errorCode")
                finishAndroid(utteranceId, Outcome.FAILED)
            }
            override fun onStop(utteranceId: String, interrupted: Boolean) {
                finishAndroid(utteranceId, if (interrupted) Outcome.INTERRUPTED else Outcome.FAILED)
            }
            override fun onRangeStart(utteranceId: String, start: Int, end: Int, frame: Int) {
                val item = current.get() ?: return
                if (item.id != utteranceId || !androidProgressKnown.get()) return
                lastSpokenEnd.accumulateAndGet(end.coerceIn(0, item.text.length), ::maxOf)
            }
        })

        packManager.prepareAsync { state ->
            onVoicePackChanged(state)
            when (state.state) {
                VoicePackManager.State.READY -> Log.i(TAG, "Voz neural local pronta: ${state.detail}")
                VoicePackManager.State.DOWNLOADING -> {
                    if (state.progressPercent == 0 || state.progressPercent == 50 || state.progressPercent == 95) {
                        Log.i(TAG, "Preparando voz neural local: ${state.progressPercent ?: 0}%")
                    }
                }
                VoicePackManager.State.FAILED -> Log.e(TAG, "Pacote neural indisponível: ${state.detail}")
                VoicePackManager.State.MISSING -> Unit
            }
        }
    }

    override fun onInit(status: Int) {
        ready.set(status == TextToSpeech.SUCCESS)
        androidInit.countDown()
        if (!ready.get()) Log.w(TAG, "Android TextToSpeech de emergência não inicializou: $status")
    }

    fun currentUtterance(): CoreClient.SpeechUtterance? = current.get()

    fun voicePackState(): VoicePackManager.Snapshot = packManager.state()

    fun speak(item: CoreClient.SpeechUtterance): Boolean {
        if (!current.compareAndSet(null, item)) return false
        stopRequested.set(false)
        androidProgressKnown.set(false)
        lastSpokenEnd.set(0)

        if (localVoice.isReady()) {
            val result = localVoice.play(
                text = item.text,
                requestedRate = item.rate,
                volume = item.volume,
                stopRequested = stopRequested,
                onStarted = { onSpeakingChanged(true) },
            )
            when (result) {
                LocalVoiceEngine.PlayResult.COMPLETED -> {
                    finish(item, Outcome.SPOKEN)
                    return true
                }
                LocalVoiceEngine.PlayResult.INTERRUPTED -> {
                    finish(item, Outcome.INTERRUPTED)
                    return true
                }
                LocalVoiceEngine.PlayResult.FAILED -> {
                    Log.w(TAG, "Voz neural falhou; usando TTS Android somente como emergência")
                }
                LocalVoiceEngine.PlayResult.UNAVAILABLE -> Unit
            }
        } else {
            val pack = packManager.state()
            Log.i(TAG, "Voz neural ainda não pronta (${pack.state}); fallback Android temporário")
        }

        if (stopRequested.get()) {
            finish(item, Outcome.INTERRUPTED)
            return true
        }
        if (!awaitAndroidReady()) {
            Log.w(TAG, "Nenhum sintetizador local disponível")
            current.compareAndSet(item, null)
            return false
        }
        return startAndroidEmergency(item)
    }

    private fun awaitAndroidReady(): Boolean {
        if (ready.get()) return true
        try {
            androidInit.await(ANDROID_TTS_INIT_WAIT_MS, TimeUnit.MILLISECONDS)
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
            return false
        }
        return ready.get()
    }

    fun stopCurrent(): CoreClient.SpeechUtterance? {
        val item = current.get() ?: return null
        stopRequested.set(true)
        localVoice.stop()
        runCatching { tts.stop() }
        finish(item, Outcome.INTERRUPTED)
        return item
    }

    private fun startAndroidEmergency(item: CoreClient.SpeechUtterance): Boolean {
        androidProgressKnown.set(true)
        lastSpokenEnd.set(0)
        val locale = Locale.forLanguageTag(identity.localeTag)
        val languageResult = tts.setLanguage(locale)
        if (languageResult == TextToSpeech.LANG_MISSING_DATA || languageResult == TextToSpeech.LANG_NOT_SUPPORTED) {
            Log.w(TAG, "Fallback Android não possui ${identity.localeTag}")
        }
        selectEmergencyVoice(locale)
        tts.setSpeechRate(item.rate.coerceIn(0.78F, 1.25F))
        tts.setPitch(item.pitch.coerceIn(0.88F, 1.14F))
        val params = Bundle().apply {
            putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, item.volume.coerceIn(0F, 1F))
        }
        val result = tts.speak(item.text, TextToSpeech.QUEUE_FLUSH, params, item.id)
        if (result == TextToSpeech.SUCCESS) {
            onSpeakingChanged(true)
            Log.w(TAG, "Falando com TTS Android de emergência; voz canônica neural indisponível")
            return true
        }
        current.compareAndSet(item, null)
        androidProgressKnown.set(false)
        return false
    }

    private fun selectEmergencyVoice(locale: Locale) {
        val voices = runCatching { tts.voices }.getOrNull().orEmpty()
        selectedFallbackVoiceName?.let { cachedName ->
            val cached = voices.firstOrNull { it.name == cachedName }
            if (cached != null) {
                runCatching { tts.voice = cached }
                return
            }
        }

        val offline = voices.filter { !it.isNetworkConnectionRequired }
        val pool = if (offline.isNotEmpty()) offline else voices.toList()
        val languageMatches = pool.filter { it.locale.language.equals("pt", ignoreCase = true) }
        val candidates = if (languageMatches.isNotEmpty()) languageMatches else pool
        val chosen = candidates.sortedWith(
            compareByDescending<android.speech.tts.Voice> {
                it.locale.toLanguageTag().equals(identity.localeTag, ignoreCase = true)
            }
                .thenByDescending { it.locale.country.equals("BR", ignoreCase = true) }
                .thenByDescending { it.quality }
                .thenBy { it.latency }
                .thenBy { it.name }
        ).firstOrNull()
        if (chosen != null) {
            runCatching { tts.voice = chosen }
                .onSuccess {
                    selectedFallbackVoiceName = chosen.name
                    Log.i(TAG, "Fallback Android fixado: ${chosen.name} locale=${chosen.locale}")
                }
                .onFailure { Log.w(TAG, "Não foi possível fixar fallback Android ${chosen.name}", it) }
        }
    }

    private fun finishAndroid(utteranceId: String, outcome: Outcome) {
        val item = current.get() ?: return
        if (item.id != utteranceId) return
        finish(item, outcome)
    }

    private fun finish(item: CoreClient.SpeechUtterance, outcome: Outcome) {
        if (!current.compareAndSet(item, null)) return
        val spokenChars = when (outcome) {
            Outcome.SPOKEN -> item.text.length
            Outcome.INTERRUPTED -> if (androidProgressKnown.get()) {
                lastSpokenEnd.get().coerceIn(0, item.text.length)
            } else null
            Outcome.FAILED, Outcome.PARTIAL -> null
        }
        androidProgressKnown.set(false)
        onSpeakingChanged(false)
        onFinished(item.id, outcome, spokenChars)
    }

    override fun close() {
        stopRequested.set(true)
        current.getAndSet(null)
        androidInit.countDown()
        localVoice.close()
        packManager.close()
        androidProgressKnown.set(false)
        selectedFallbackVoiceName = null
        ready.set(false)
        runCatching { tts.stop() }
        runCatching { tts.shutdown() }
        onSpeakingChanged(false)
    }

    companion object {
        private const val ANDROID_TTS_INIT_WAIT_MS = 2_500L
        private const val TAG = "AmoraVoice"
    }
}

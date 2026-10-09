package dev.amora.chibi.voice

import android.content.Context
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import dev.amora.chibi.core.CoreClient
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean

class VoiceOutput(
    context: Context,
    private val onSpeakingChanged: (Boolean) -> Unit,
    private val onFinished: (String, Boolean) -> Unit,
) : TextToSpeech.OnInitListener, AutoCloseable {
    private val ready = AtomicBoolean(false)
    private val tts = TextToSpeech(context.applicationContext, this)

    init {
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String) {
                onSpeakingChanged(true)
            }

            override fun onDone(utteranceId: String) {
                onSpeakingChanged(false)
                onFinished(utteranceId, true)
            }

            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String) {
                onSpeakingChanged(false)
                onFinished(utteranceId, false)
            }

            override fun onError(utteranceId: String, errorCode: Int) {
                Log.w(TAG, "TTS falhou id=$utteranceId code=$errorCode")
                onSpeakingChanged(false)
                onFinished(utteranceId, false)
            }
        })
    }

    override fun onInit(status: Int) {
        ready.set(status == TextToSpeech.SUCCESS)
        if (!ready.get()) Log.w(TAG, "Android TextToSpeech não inicializou: $status")
    }

    fun speak(item: CoreClient.SpeechUtterance): Boolean {
        if (!ready.get()) return false
        val locale = Locale.forLanguageTag(item.locale)
        val languageResult = tts.setLanguage(locale)
        if (languageResult == TextToSpeech.LANG_MISSING_DATA || languageResult == TextToSpeech.LANG_NOT_SUPPORTED) {
            Log.w(TAG, "Voz/locale não suportado: ${item.locale}")
        }
        tts.setSpeechRate(item.rate.coerceIn(0.5F, 2.0F))
        tts.setPitch(item.pitch.coerceIn(0.5F, 2.0F))
        val params = Bundle().apply {
            putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, item.volume.coerceIn(0F, 1F))
        }
        val result = tts.speak(item.text, TextToSpeech.QUEUE_FLUSH, params, item.id)
        if (result == TextToSpeech.SUCCESS) {
            onSpeakingChanged(true)
            return true
        }
        return false
    }

    override fun close() {
        ready.set(false)
        runCatching { tts.stop() }
        runCatching { tts.shutdown() }
        onSpeakingChanged(false)
    }

    companion object {
        private const val TAG = "AmoraVoice"
    }
}

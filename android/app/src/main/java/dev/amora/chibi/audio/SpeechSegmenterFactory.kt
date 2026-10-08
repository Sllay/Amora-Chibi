package dev.amora.chibi.audio

import android.content.Context
import android.util.Log

object SpeechSegmenterFactory {
    fun create(context: Context): SpeechSegmenter = try {
        SherpaSpeechSegmenter(context)
    } catch (error: Throwable) {
        Log.w("AmoraAudio", "Sherpa VAD indisponível; usando Energy fallback", error)
        EnergySpeechSegmenter()
    }
}

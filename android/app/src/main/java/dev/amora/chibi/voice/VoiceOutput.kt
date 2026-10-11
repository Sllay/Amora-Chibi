package dev.amora.chibi.voice

import android.content.Context
import android.util.Log
import dev.amora.chibi.core.CoreClient
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/**
 * Authoritative V1 voice output.
 *
 * The provisional neural identity is bundled with the APK. Android TextToSpeech
 * is intentionally not used as an automatic fallback here: a hidden fallback
 * changes Amora's identity and masks physical integration failures.
 */
class VoiceOutput(
    context: Context,
    private val onSpeakingChanged: (Boolean) -> Unit,
    private val onFinished: (String, Outcome, Int?) -> Unit,
    private val onVoiceStatusChanged: (VoiceDiagnostics.Snapshot) -> Unit = {},
) : AutoCloseable {
    enum class Outcome { SPOKEN, FAILED, INTERRUPTED, PARTIAL }

    private val identity = VoiceIdentity.AMORA_V1_PROVISIONAL
    private val diagnostics = VoiceDiagnostics(context.applicationContext)
    private val stopRequested = AtomicBoolean(false)
    private val current = AtomicReference<CoreClient.SpeechUtterance?>(null)
    private val localVoice = LocalVoiceEngine(
        context = context.applicationContext,
        identity = identity,
        diagnostics = diagnostics,
        onStatusChanged = onVoiceStatusChanged,
    )

    init {
        if (localVoice.assetsAvailable()) {
            diagnostics.record(VoiceDiagnostics.STAGE_ASSETS_READY, "voz provisória empacotada no APK")
        } else {
            diagnostics.record(VoiceDiagnostics.STAGE_FAILED, "assets da voz provisória não estão no APK")
        }
        onVoiceStatusChanged(diagnostics.snapshot())
    }

    fun currentUtterance(): CoreClient.SpeechUtterance? = current.get()

    fun speak(item: CoreClient.SpeechUtterance): Boolean {
        if (!current.compareAndSet(null, item)) return false
        stopRequested.set(false)

        val result = localVoice.play(
            text = item.text,
            requestedRate = item.rate,
            volume = item.volume,
            stopRequested = stopRequested,
            onStarted = { onSpeakingChanged(true) },
        )
        when (result) {
            LocalVoiceEngine.PlayResult.COMPLETED -> finish(item, Outcome.SPOKEN)
            LocalVoiceEngine.PlayResult.INTERRUPTED -> finish(item, Outcome.INTERRUPTED)
            LocalVoiceEngine.PlayResult.UNAVAILABLE,
            LocalVoiceEngine.PlayResult.FAILED -> {
                Log.e(TAG, "Voz neural local indisponível: ${localVoice.lastFailure()}")
                finish(item, Outcome.FAILED)
            }
        }
        return true
    }

    fun stopCurrent(): CoreClient.SpeechUtterance? {
        val item = current.get() ?: return null
        stopRequested.set(true)
        localVoice.stop()
        finish(item, Outcome.INTERRUPTED)
        return item
    }

    private fun finish(item: CoreClient.SpeechUtterance, outcome: Outcome) {
        if (!current.compareAndSet(item, null)) return
        onSpeakingChanged(false)
        val spokenChars = if (outcome == Outcome.SPOKEN) item.text.length else null
        onFinished(item.id, outcome, spokenChars)
    }

    override fun close() {
        stopRequested.set(true)
        current.getAndSet(null)
        localVoice.close()
        onSpeakingChanged(false)
    }

    companion object {
        private const val TAG = "AmoraVoice"
    }
}

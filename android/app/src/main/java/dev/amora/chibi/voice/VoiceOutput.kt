package dev.amora.chibi.voice

import android.content.Context
import android.util.Log
import dev.amora.chibi.core.CoreClient
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/**
 * Physical-gate voice output for the approved Amora laboratory stack.
 *
 * This dev build intentionally promotes the candidate inside Android so BK can
 * test the real conversation path on Galaxy A15. It is NOT a canonical release
 * promotion until that device gate passes. There is no hidden Faber/system-TTS
 * fallback because a fallback would mask integration regressions.
 */
class VoiceOutput(
    context: Context,
    private val onSpeakingChanged: (Boolean) -> Unit,
    private val onFinished: (String, Outcome, Int?) -> Unit,
    private val onVoiceStatusChanged: (VoiceDiagnostics.Snapshot) -> Unit = {},
) : AutoCloseable {
    enum class Outcome { SPOKEN, FAILED, INTERRUPTED, PARTIAL }

    private val diagnostics = VoiceDiagnostics(context.applicationContext)
    private val stopRequested = AtomicBoolean(false)
    private val current = AtomicReference<CoreClient.SpeechUtterance?>(null)
    private val candidateVoice = AmoraSupertonicVoiceEngine(
        context = context.applicationContext,
        diagnostics = diagnostics,
        onStatusChanged = onVoiceStatusChanged,
    )

    init {
        if (candidateVoice.assetsAvailable()) {
            diagnostics.record(
                VoiceDiagnostics.STAGE_ASSETS_READY,
                "FINAL_B + VoiceSignature V1 + Emotion V2 empacotados para gate físico",
            )
        } else {
            diagnostics.record(VoiceDiagnostics.STAGE_FAILED, "assets da candidata Amora não estão completos no APK")
        }
        onVoiceStatusChanged(diagnostics.snapshot())
    }

    fun currentUtterance(): CoreClient.SpeechUtterance? = current.get()

    fun speak(item: CoreClient.SpeechUtterance): Boolean {
        if (!current.compareAndSet(null, item)) return false
        stopRequested.set(false)

        // Core remains the owner of emotional state. Android only maps the V/A/D
        // carried by the utterance into the frozen V2 expression contract.
        val expression = runCatching {
            AmoraVoiceExpressionPlanner.fromVad(
                valence = item.valence,
                arousal = item.arousal,
                dominance = item.dominance,
            )
        }.getOrElse { AmoraVoiceExpression.NEUTRAL }

        val result = candidateVoice.play(
            text = item.text,
            expression = expression,
            volume = item.volume,
            stopRequested = stopRequested,
            onStarted = { onSpeakingChanged(true) },
        )
        when (result) {
            AmoraSupertonicVoiceEngine.PlayResult.COMPLETED -> finish(item, Outcome.SPOKEN)
            AmoraSupertonicVoiceEngine.PlayResult.INTERRUPTED -> finish(item, Outcome.INTERRUPTED)
            AmoraSupertonicVoiceEngine.PlayResult.UNAVAILABLE,
            AmoraSupertonicVoiceEngine.PlayResult.FAILED -> {
                Log.e(TAG, "Candidata de voz indisponível: ${candidateVoice.lastFailure()}")
                finish(item, Outcome.FAILED)
            }
        }
        return true
    }

    fun stopCurrent(): CoreClient.SpeechUtterance? {
        val item = current.get() ?: return null
        stopRequested.set(true)
        candidateVoice.stop()
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
        candidateVoice.close()
        onSpeakingChanged(false)
    }

    companion object {
        private const val TAG = "AmoraVoice"
    }
}

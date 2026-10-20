package dev.amora.chibi.voice

import android.content.Context
import android.util.Log
import dev.amora.chibi.core.CoreClient
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/**
 * Physical-gate voice output for the approved Amora voice stack.
 *
 * Rendering and playback are separated so AudioCaptureService may pre-render the
 * next segment from the same cognitive turn while the current segment is playing.
 * The Core still owns emotional expression and Android never re-classifies V/A/D.
 */
class VoiceOutput(
    context: Context,
    private val onTurnActiveChanged: (Boolean) -> Unit,
    private val onSpeakingChanged: (Boolean) -> Unit,
    private val onAudioStarted: (String) -> Unit,
    private val onFinished: (String, Outcome, Int?) -> Unit,
    private val onVoiceStatusChanged: (VoiceDiagnostics.Snapshot) -> Unit = {},
) : AutoCloseable {
    enum class Outcome { SPOKEN, FAILED, INTERRUPTED, PARTIAL }

    private val diagnostics = VoiceDiagnostics(context.applicationContext)
    private val stopRequested = AtomicBoolean(false)
    private val current = AtomicReference<CoreClient.SpeechUtterance?>(null)
    private val preparedLock = Any()
    private val prepared = mutableMapOf<String, AmoraSupertonicVoiceEngine.PreparedAudio>()
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

    fun isPrepared(utteranceId: String): Boolean = synchronized(preparedLock) {
        prepared.containsKey(utteranceId)
    }

    /** Render one future segment without taking the audio floor. */
    fun prefetch(item: CoreClient.SpeechUtterance): Boolean {
        if (stopRequested.get()) return false
        if (isPrepared(item.id)) return true
        return when (
            val result = candidateVoice.prepareAudio(
                text = item.text,
                expression = expressionFor(item),
                volume = item.volume,
                stopRequested = stopRequested,
            )
        ) {
            is AmoraSupertonicVoiceEngine.PrepareResult.Ready -> {
                synchronized(preparedLock) {
                    // Only one look-ahead segment is useful. Keeping the cache tiny
                    // prevents long stories from retaining large PCM arrays in RAM.
                    prepared.clear()
                    prepared[item.id] = result.audio
                }
                true
            }
            AmoraSupertonicVoiceEngine.PrepareResult.Interrupted -> false
            AmoraSupertonicVoiceEngine.PrepareResult.Unavailable,
            AmoraSupertonicVoiceEngine.PrepareResult.Failed -> {
                Log.e(TAG, "Prefetch de voz falhou para ${item.id}: ${candidateVoice.lastFailure()}")
                false
            }
        }
    }

    fun speak(item: CoreClient.SpeechUtterance): Boolean {
        if (!current.compareAndSet(null, item)) return false
        stopRequested.set(false)
        onTurnActiveChanged(true)

        val ready = synchronized(preparedLock) { prepared.remove(item.id) }
            ?: when (
                val result = candidateVoice.prepareAudio(
                    text = item.text,
                    expression = expressionFor(item),
                    volume = item.volume,
                    stopRequested = stopRequested,
                )
            ) {
                is AmoraSupertonicVoiceEngine.PrepareResult.Ready -> result.audio
                AmoraSupertonicVoiceEngine.PrepareResult.Interrupted -> {
                    finish(item, Outcome.INTERRUPTED)
                    return true
                }
                AmoraSupertonicVoiceEngine.PrepareResult.Unavailable,
                AmoraSupertonicVoiceEngine.PrepareResult.Failed -> {
                    Log.e(TAG, "Candidata de voz indisponível: ${candidateVoice.lastFailure()}")
                    finish(item, Outcome.FAILED)
                    return true
                }
            }

        val result = candidateVoice.playPrepared(
            prepared = ready,
            stopRequested = stopRequested,
            onStarted = {
                onSpeakingChanged(true)
                onAudioStarted(item.id)
            },
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
        discardPrepared()
        // Finish immediately from the capture thread so the microphone can recover
        // the barge-in pre-roll without waiting for a cancelled neural call to unwind.
        finish(item, Outcome.INTERRUPTED)
        return item
    }

    fun discardPrepared() {
        synchronized(preparedLock) { prepared.clear() }
    }

    private fun expressionFor(item: CoreClient.SpeechUtterance): AmoraVoiceExpression = item.expression?.let { value ->
        AmoraVoiceExpression(
            id = value.id,
            rateScale = value.rateScale,
            pauseScale = value.pauseScale,
            energyScale = value.energyScale,
            pitchRangeScale = value.pitchRangeScale,
            f0RegisterScale = value.f0RegisterScale,
            softness = value.softness,
            firmness = value.firmness,
            label = value.sourceEmotion?.uppercase() ?: if (value.fallback) "NEUTRA/FAILSAFE" else "CORE",
        )
    } ?: AmoraVoiceExpression.NEUTRAL

    private fun finish(item: CoreClient.SpeechUtterance, outcome: Outcome) {
        if (!current.compareAndSet(item, null)) return
        onSpeakingChanged(false)
        onTurnActiveChanged(false)
        val spokenChars = if (outcome == Outcome.SPOKEN) item.text.length else null
        onFinished(item.id, outcome, spokenChars)
    }

    override fun close() {
        stopRequested.set(true)
        current.getAndSet(null)
        discardPrepared()
        candidateVoice.close()
        onSpeakingChanged(false)
        onTurnActiveChanged(false)
    }

    companion object {
        private const val TAG = "AmoraVoice"
    }
}

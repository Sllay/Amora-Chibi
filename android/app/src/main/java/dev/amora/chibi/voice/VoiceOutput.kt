package dev.amora.chibi.voice

import android.content.Context
import android.util.Log
import dev.amora.chibi.core.CoreClient
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/**
 * Authoritative V1 voice output.
 *
 * Speech stays local/offline. The paired local Core only serves the small,
 * versioned VoiceProfile used to tune Amora's base signature and bounded prosody.
 */
class VoiceOutput(
    context: Context,
    private val coreClient: CoreClient,
    private val onSpeakingChanged: (Boolean) -> Unit,
    private val onFinished: (String, Outcome, Int?) -> Unit,
    private val onVoiceStatusChanged: (VoiceDiagnostics.Snapshot) -> Unit = {},
) : AutoCloseable {
    enum class Outcome { SPOKEN, FAILED, INTERRUPTED, PARTIAL }

    private val identity = VoiceIdentity.AMORA_V1
    private val diagnostics = VoiceDiagnostics(context.applicationContext)
    private val profileRepository = VoiceProfileRepository(context.applicationContext, identity)
    private val stopRequested = AtomicBoolean(false)
    private val current = AtomicReference<CoreClient.SpeechUtterance?>(null)
    private val localVoice = LocalVoiceEngine(
        context = context.applicationContext,
        identity = identity,
        diagnostics = diagnostics,
        onStatusChanged = onVoiceStatusChanged,
    )

    init {
        val profile = profileRepository.current()
        if (localVoice.assetsAvailable()) {
            diagnostics.record(
                VoiceDiagnostics.STAGE_ASSETS_READY,
                "Supertonic speaker ${identity.speakerId} · profile ${profile.profile.activeProfile} r${profile.profile.revision} (${profile.source})",
            )
        } else {
            diagnostics.record(VoiceDiagnostics.STAGE_FAILED, "assets Supertonic da Amora não estão no APK")
        }
        onVoiceStatusChanged(diagnostics.snapshot())
    }

    fun currentUtterance(): CoreClient.SpeechUtterance? = current.get()

    /** Safe to call periodically off the audio thread. */
    fun refreshProfile(): VoiceProfileRepository.Snapshot {
        val updated = profileRepository.refresh(coreClient)
        diagnostics.record(
            VoiceDiagnostics.STAGE_READY,
            "VoiceProfile ${updated.profile.activeProfile} r${updated.profile.revision} · ${updated.source}",
        )
        onVoiceStatusChanged(diagnostics.snapshot())
        return updated
    }

    fun speak(item: CoreClient.SpeechUtterance): Boolean {
        if (!current.compareAndSet(null, item)) return false
        stopRequested.set(false)
        val profile = profileRepository.current().profile

        val result = localVoice.play(
            text = item.text,
            requestedRate = item.rate,
            requestedPitch = item.pitch,
            valence = item.valence,
            arousal = item.arousal,
            volume = item.volume,
            profile = profile,
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

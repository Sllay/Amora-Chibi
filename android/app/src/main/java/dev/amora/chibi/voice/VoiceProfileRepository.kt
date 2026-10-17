package dev.amora.chibi.voice

import android.content.Context
import android.util.Log
import dev.amora.chibi.core.CoreClient

/**
 * Keeps the last validated canonical tuning profile locally and revalidates it
 * against the paired local Core with ETag. A bad/missing update never replaces a
 * previously good identity profile and never disables offline speech.
 */
class VoiceProfileRepository(
    context: Context,
    private val identity: VoiceIdentity = VoiceIdentity.AMORA_V1,
) {
    data class Snapshot(
        val profile: VoiceProfileConfig,
        val etag: String?,
        val source: String,
    )

    private val prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    @Volatile private var snapshot: Snapshot = loadCached()

    fun current(): Snapshot = snapshot

    @Synchronized
    fun refresh(client: CoreClient): Snapshot {
        val previous = snapshot
        return try {
            val result = client.fetchVoiceProfile(previous.etag)
            if (result.notModified) {
                if (result.etag != null && result.etag != previous.etag) {
                    snapshot = previous.copy(etag = result.etag)
                }
                snapshot
            } else {
                val raw = requireNotNull(result.json) { "Core respondeu profile sem conteúdo" }
                val parsed = VoiceProfileConfig.parse(raw).validateFor(identity)
                val next = Snapshot(parsed, result.etag, "core")
                prefs.edit()
                    .putString(KEY_JSON, raw)
                    .putString(KEY_ETAG, result.etag)
                    .apply()
                snapshot = next
                Log.i(TAG, "Voice profile atualizado: ${parsed.profileId} r${parsed.revision} ${parsed.activeProfile}")
                next
            }
        } catch (error: Throwable) {
            Log.w(TAG, "Voice profile remoto inválido/indisponível; mantendo ${previous.source}", error)
            previous
        }
    }

    private fun loadCached(): Snapshot {
        val raw = prefs.getString(KEY_JSON, null)
        if (!raw.isNullOrBlank()) {
            runCatching { VoiceProfileConfig.parse(raw).validateFor(identity) }
                .onSuccess { profile ->
                    return Snapshot(profile, prefs.getString(KEY_ETAG, null), "cache")
                }
                .onFailure { Log.w(TAG, "Cache de voice profile inválido; usando default seguro", it) }
        }
        return Snapshot(VoiceProfileConfig.safeDefault(identity), null, "apk-default")
    }

    companion object {
        private const val TAG = "AmoraVoiceProfile"
        private const val PREFS = "amora_voice_profile"
        private const val KEY_JSON = "validated_json"
        private const val KEY_ETAG = "etag"
    }
}

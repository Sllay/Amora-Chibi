package dev.amora.chibi.voice

import android.content.Context
import dev.amora.chibi.core.CoreClient

data class VoiceProfileSnapshot(
    val catalog: VoiceProfileCatalog,
    val source: String,
    val etag: String?,
)

/**
 * Small cache for mutable voice tuning. Model weights stay in the APK; only the
 * bounded profile/signature parameters are refreshed from the paired Core.
 */
class VoiceProfileStore(context: Context) {
    private val preferences = context.applicationContext.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)

    fun load(expectedModelId: String? = null): VoiceProfileSnapshot {
        val raw = preferences.getString(KEY_JSON, null)
        if (!raw.isNullOrBlank()) {
            val cached = runCatching { VoiceProfileCatalog.parse(raw) }.getOrNull()
            if (cached != null && (expectedModelId == null || cached.modelId == expectedModelId)) {
                return VoiceProfileSnapshot(
                    catalog = cached,
                    source = "cache",
                    etag = preferences.getString(KEY_ETAG, null),
                )
            }
        }
        return VoiceProfileSnapshot(
            catalog = VoiceProfileCatalog.builtIn(),
            source = "APK",
            etag = null,
        )
    }

    fun refresh(client: CoreClient, expectedModelId: String): VoiceProfileSnapshot {
        val current = load(expectedModelId)
        val response = client.fetchVoiceProfiles(current.etag)
        if (response.notModified) {
            return current.copy(source = if (current.source == "APK") "APK" else "Core/cache")
        }

        val raw = requireNotNull(response.body) { "Core respondeu catálogo vocal sem body" }
        val parsed = VoiceProfileCatalog.parse(raw)
        require(parsed.modelId == expectedModelId) {
            "Core enviou perfil para ${parsed.modelId}; APK contém $expectedModelId"
        }
        preferences.edit()
            .putString(KEY_JSON, raw)
            .putString(KEY_ETAG, response.etag)
            .apply()
        return VoiceProfileSnapshot(
            catalog = parsed,
            source = "Core",
            etag = response.etag,
        )
    }

    companion object {
        private const val PREFERENCES = "amora_voice_profiles"
        private const val KEY_JSON = "catalog_json"
        private const val KEY_ETAG = "catalog_etag"
    }
}

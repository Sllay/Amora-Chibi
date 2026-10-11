package dev.amora.chibi.voice

import android.content.Context
import android.os.Build

/** Small persistent diagnostic surface for the physical V1 voice gate. */
class VoiceDiagnostics(context: Context) {
    data class Snapshot(
        val stage: String,
        val detail: String,
        val updatedAtMs: Long,
    )

    private val preferences = context.applicationContext.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)

    fun record(stage: String, detail: String) {
        preferences.edit()
            .putString(KEY_STAGE, stage.take(48))
            .putString(KEY_DETAIL, detail.take(320))
            .putLong(KEY_UPDATED_AT, System.currentTimeMillis())
            .apply()
    }

    fun snapshot(): Snapshot = Snapshot(
        stage = preferences.getString(KEY_STAGE, STAGE_NOT_STARTED) ?: STAGE_NOT_STARTED,
        detail = preferences.getString(KEY_DETAIL, "aguardando inicialização da voz") ?: "",
        updatedAtMs = preferences.getLong(KEY_UPDATED_AT, 0L),
    )

    fun describe(identity: VoiceIdentity = VoiceIdentity.AMORA_V1_PROVISIONAL): String {
        val current = snapshot()
        val abi = Build.SUPPORTED_ABIS.joinToString().ifBlank { "desconhecida" }
        return buildString {
            append("Backend: sherpa-onnx 1.13.8 · local/offline\n")
            append("Voz provisória: ${identity.packId}\n")
            append("ABI: $abi\n")
            append("Estado: ${current.stage}\n")
            append("Detalhe: ${current.detail}")
        }
    }

    companion object {
        const val STAGE_NOT_STARTED = "NÃO INICIADA"
        const val STAGE_ASSETS_READY = "ASSETS OK"
        const val STAGE_PREPARING_DATA = "PREPARANDO ESPEAK"
        const val STAGE_ENGINE_READY = "ENGINE OK"
        const val STAGE_SYNTHESIZING = "SINTETIZANDO"
        const val STAGE_PLAYING = "REPRODUZINDO"
        const val STAGE_READY = "VOZ NEURAL OK"
        const val STAGE_FAILED = "FALHA NEURAL"

        private const val PREFERENCES = "amora_voice_diagnostics"
        private const val KEY_STAGE = "stage"
        private const val KEY_DETAIL = "detail"
        private const val KEY_UPDATED_AT = "updated_at"
    }
}

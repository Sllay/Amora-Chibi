package dev.amora.chibi.core

import android.content.Context
import dev.amora.chibi.security.SecureTokenStore
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

class CoreClient(context: Context) {
    data class TranscriptionResult(
        val text: String,
        val addresseeId: String?,
        val replyQueued: Boolean,
        val accepted: Boolean,
    )

    data class VoiceExpression(
        val id: String,
        val rateScale: Float,
        val pauseScale: Float,
        val energyScale: Float,
        val pitchRangeScale: Float,
        val f0RegisterScale: Float,
        val softness: Float,
        val firmness: Float,
        val sourceEmotion: String?,
        val sourceIntensity: Float,
        val fallback: Boolean,
    )

    data class SpeechUtterance(
        val id: String,
        val text: String,
        val locale: String,
        val rate: Float,
        val pitch: Float,
        val volume: Float,
        val valence: Float,
        val arousal: Float,
        val dominance: Float,
        val expression: VoiceExpression?,
        val remoteTtsAllowed: Boolean,
        val cognitiveTurnId: String?,
    )

    data class VoiceStreamResult(
        val usedRemote: Boolean,
        val completed: Boolean,
        val bytesReceived: Long,
    )

    data class VoiceProfileSyncResult(
        val notModified: Boolean,
        val body: String?,
        val etag: String?,
        val revision: String?,
    )

    private val appContext = context.applicationContext
    private val tokenStore = SecureTokenStore(appContext)
    private val privacyPreferences = appContext.getSharedPreferences(PRIVACY_PREFERENCES, Context.MODE_PRIVATE)
    private val baseUrl = "http://127.0.0.1:8765"

    fun isPaired(): Boolean = tokenStore.hasToken()

    fun isRemoteCognitionAllowed(): Boolean = privacyPreferences.getBoolean(KEY_REMOTE_COGNITION, false)

    fun setRemoteCognitionAllowed(allowed: Boolean) {
        privacyPreferences.edit().putBoolean(KEY_REMOTE_COGNITION, allowed).apply()
    }

    fun pair(code: String) {
        require(code.matches(Regex("\\d{6}"))) { "Código deve ter 6 dígitos" }
        val connection = open("/v1/pairing/redeem", "POST")
        connection.setRequestProperty("Content-Type", "application/json")
        connection.doOutput = true
        connection.outputStream.use { it.write(JSONObject().put("code", code).toString().toByteArray(Charsets.UTF_8)) }
        val body = readResponse(connection)
        tokenStore.save(JSONObject(body).getString("token"))
    }

    fun health(): String {
        val connection = open("/health", "GET")
        return JSONObject(readResponse(connection)).optString("status", "unknown")
    }

    fun transcribe(wav: ByteArray): TranscriptionResult {
        val speaker = URLEncoder.encode("SESSION_ANDROID_SPEAKER_1", "UTF-8")
        val remoteCognition = isRemoteCognitionAllowed()
        val path = "/v1/audio/transcribe?language=pt&speaker_id=$speaker&speaker_confidence=0.35&remote_processing_allowed=$remoteCognition"
        val connection = open(path, "POST", authenticated = true)
        connection.setRequestProperty("Content-Type", "audio/wav")
        connection.doOutput = true
        connection.outputStream.use { it.write(wav) }
        val root = JSONObject(readResponse(connection))
        val accepted = root.optBoolean("accepted", true)
        val transcript = root.optJSONObject("transcript")
        val text = transcript?.optString("text").orEmpty()
        val addressee = root.optJSONObject("observation")
            ?.optJSONObject("addressee")
            ?.optString("entity_id")
            ?.takeIf { it.isNotBlank() }
        return TranscriptionResult(text, addressee, !root.isNull("reply_queued"), accepted)
    }

    fun nextUtterance(
        deviceId: String = "DEVICE_A15",
        cognitiveTurnId: String? = null,
    ): SpeechUtterance? {
        val device = URLEncoder.encode(deviceId, "UTF-8")
        val turn = cognitiveTurnId?.takeIf { it.isNotBlank() }?.let { URLEncoder.encode(it, "UTF-8") }
        val path = buildString {
            append("/v1/conversation/next?device_id=")
            append(device)
            if (turn != null) {
                append("&cognitive_turn_id=")
                append(turn)
            }
        }
        val connection = open(path, "GET", authenticated = true)
        val root = JSONObject(readResponse(connection))
        if (root.isNull("utterance")) return null
        return parseSpeechUtterance(root.getJSONObject("utterance"))
    }

    fun markUtteranceStarted(utteranceId: String) {
        val safeId = URLEncoder.encode(utteranceId, "UTF-8")
        val connection = open("/v1/conversation/utterances/$safeId/started", "POST", authenticated = true)
        connection.setRequestProperty("Content-Type", "application/json")
        connection.doOutput = true
        connection.outputStream.use { it.write("{}".toByteArray(Charsets.UTF_8)) }
        readResponse(connection)
    }

    private fun parseSpeechUtterance(item: JSONObject): SpeechUtterance {
        val style = item.optJSONObject("style") ?: JSONObject()
        val metadata = item.optJSONObject("metadata") ?: JSONObject()
        val expression = style.optJSONObject("expression")?.let { value ->
            VoiceExpression(
                id = value.optString("id", "AMORA_VOICE_EXPRESSION_V2"),
                rateScale = value.optDouble("rate_scale", 1.0).toFloat().coerceIn(0.90f, 1.08f),
                pauseScale = value.optDouble("pause_scale", 1.0).toFloat().coerceIn(0.88f, 1.16f),
                energyScale = value.optDouble("energy_scale", 1.0).toFloat().coerceIn(0.88f, 1.06f),
                pitchRangeScale = value.optDouble("pitch_range_scale", 1.0).toFloat().coerceIn(0.90f, 1.08f),
                f0RegisterScale = value.optDouble("f0_register_scale", 1.0).toFloat().coerceIn(0.97f, 1.02f),
                softness = value.optDouble("softness", 0.25).toFloat().coerceIn(0f, 1f),
                firmness = value.optDouble("firmness", 0.15).toFloat().coerceIn(0f, 1f),
                sourceEmotion = value.optString("source_emotion").takeIf { it.isNotBlank() && it != "null" },
                sourceIntensity = value.optDouble("source_intensity", 0.0).toFloat().coerceIn(0f, 1f),
                fallback = value.optBoolean("fallback", false),
            )
        }
        return SpeechUtterance(
            id = item.getString("id"),
            text = item.getString("text"),
            locale = style.optString("locale", "pt-BR"),
            rate = style.optDouble("rate", 1.0).toFloat(),
            pitch = style.optDouble("pitch", 1.0).toFloat(),
            volume = style.optDouble("volume", 1.0).toFloat(),
            valence = style.optDouble("valence", 0.0).toFloat(),
            arousal = style.optDouble("arousal", 0.0).toFloat(),
            dominance = style.optDouble("dominance", 0.0).toFloat(),
            expression = expression,
            remoteTtsAllowed = metadata.optBoolean("remote_tts_allowed", false),
            cognitiveTurnId = metadata.optString("cognitive_turn_id").takeIf { it.isNotBlank() },
        )
    }

    fun fetchVoiceProfiles(ifNoneMatch: String? = null): VoiceProfileSyncResult {
        val connection = open("/v1/voice/profiles", "GET", authenticated = true)
        if (!ifNoneMatch.isNullOrBlank()) connection.setRequestProperty("If-None-Match", ifNoneMatch)
        try {
            val code = connection.responseCode
            val etag = connection.getHeaderField("ETag")?.takeIf { it.isNotBlank() }
            val revision = connection.getHeaderField("X-Amora-Voice-Revision")?.takeIf { it.isNotBlank() }
            if (code == HttpURLConnection.HTTP_NOT_MODIFIED) {
                return VoiceProfileSyncResult(
                    notModified = true,
                    body = null,
                    etag = etag ?: ifNoneMatch,
                    revision = revision,
                )
            }
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val body = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            if (code !in 200..299) throw IllegalStateException("Core respondeu HTTP $code: $body")
            return VoiceProfileSyncResult(
                notModified = false,
                body = body,
                etag = etag,
                revision = revision,
            )
        } finally {
            connection.disconnect()
        }
    }

    fun streamVoiceAudio(
        utteranceId: String,
        onChunk: (ByteArray) -> Boolean,
    ): VoiceStreamResult {
        val safeId = URLEncoder.encode(utteranceId, "UTF-8")
        val connection = open("/v1/voice/utterances/$safeId/audio", "GET", authenticated = true)
        connection.readTimeout = 60_000
        var received = 0L
        try {
            val code = connection.responseCode
            if (code == 409 || code == 503 || code == 404) {
                connection.errorStream?.close()
                return VoiceStreamResult(usedRemote = false, completed = false, bytesReceived = 0)
            }
            if (code !in 200..299) {
                val body = connection.errorStream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
                throw IllegalStateException("Core TTS respondeu HTTP $code: $body")
            }
            val buffer = ByteArray(16 * 1024)
            try {
                connection.inputStream.use { input ->
                    while (true) {
                        val count = input.read(buffer)
                        if (count < 0) break
                        if (count == 0) continue
                        received += count
                        if (!onChunk(buffer.copyOf(count))) {
                            return VoiceStreamResult(usedRemote = true, completed = false, bytesReceived = received)
                        }
                    }
                }
            } catch (error: IOException) {
                if (received > 0) return VoiceStreamResult(usedRemote = true, completed = false, bytesReceived = received)
                throw error
            }
            return VoiceStreamResult(usedRemote = true, completed = true, bytesReceived = received)
        } finally {
            connection.disconnect()
        }
    }

    fun acknowledgeUtterance(utteranceId: String, spoken: Boolean) {
        val safeId = URLEncoder.encode(utteranceId, "UTF-8")
        val connection = open("/v1/conversation/utterances/$safeId/ack", "POST", authenticated = true)
        connection.setRequestProperty("Content-Type", "application/json")
        connection.doOutput = true
        val payload = JSONObject().put("spoken", spoken).toString().toByteArray(Charsets.UTF_8)
        connection.outputStream.use { it.write(payload) }
        readResponse(connection)
    }

    fun cancelUtterance(utteranceId: String, reason: String = "barge_in", spokenChars: Int? = null) {
        val safeId = URLEncoder.encode(utteranceId, "UTF-8")
        val connection = open("/v1/conversation/utterances/$safeId/cancel", "POST", authenticated = true)
        connection.setRequestProperty("Content-Type", "application/json")
        connection.doOutput = true
        val root = JSONObject().put("reason", reason.take(96))
        if (spokenChars != null) root.put("spoken_chars", spokenChars.coerceAtLeast(0))
        val payload = root.toString().toByteArray(Charsets.UTF_8)
        connection.outputStream.use { it.write(payload) }
        readResponse(connection)
    }

    private fun open(path: String, method: String, authenticated: Boolean = false): HttpURLConnection {
        require(path.startsWith("/"))
        val connection = URL(baseUrl + path).openConnection() as HttpURLConnection
        connection.requestMethod = method
        connection.connectTimeout = 4_000
        connection.readTimeout = 35_000
        connection.useCaches = false
        if (authenticated) {
            val token = tokenStore.load() ?: error("App ainda não está pareado com o Core")
            connection.setRequestProperty("Authorization", "Bearer $token")
        }
        return connection
    }

    private fun readResponse(connection: HttpURLConnection): String {
        val code = connection.responseCode
        val stream = if (code in 200..299) connection.inputStream else connection.errorStream
        val body = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
        connection.disconnect()
        if (code !in 200..299) throw IllegalStateException("Core respondeu HTTP $code: $body")
        return body
    }

    companion object {
        private const val PRIVACY_PREFERENCES = "amora_privacy"
        private const val KEY_REMOTE_COGNITION = "remote_cognition_allowed"
    }
}

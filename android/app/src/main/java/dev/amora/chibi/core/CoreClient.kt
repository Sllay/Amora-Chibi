package dev.amora.chibi.core

import android.content.Context
import dev.amora.chibi.security.SecureTokenStore
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

class CoreClient(context: Context) {
    data class TranscriptionResult(val text: String, val addresseeId: String?)

    private val tokenStore = SecureTokenStore(context.applicationContext)
    private val baseUrl = "http://127.0.0.1:8765"

    fun isPaired(): Boolean = tokenStore.hasToken()

    fun pair(code: String) {
        require(code.matches(Regex("\\d{6}"))) { "Código deve ter 6 dígitos" }
        val connection = open("/v1/pairing/redeem", "POST")
        connection.setRequestProperty("Content-Type", "application/json")
        connection.doOutput = true
        connection.outputStream.use {
            it.write(JSONObject().put("code", code).toString().toByteArray(Charsets.UTF_8))
        }
        val body = readResponse(connection)
        val token = JSONObject(body).getString("token")
        tokenStore.save(token)
    }

    fun health(): String {
        val connection = open("/health", "GET")
        return JSONObject(readResponse(connection)).optString("status", "unknown")
    }

    fun transcribe(wav: ByteArray): TranscriptionResult {
        val speaker = URLEncoder.encode("SESSION_ANDROID_SPEAKER_1", "UTF-8")
        val path = "/v1/audio/transcribe?language=pt&speaker_id=$speaker&speaker_confidence=0.35"
        val connection = open(path, "POST", authenticated = true)
        connection.setRequestProperty("Content-Type", "audio/wav")
        connection.doOutput = true
        connection.outputStream.use { it.write(wav) }
        val root = JSONObject(readResponse(connection))
        val text = root.getJSONObject("transcript").optString("text")
        val addressee = root.getJSONObject("observation").optJSONObject("addressee")
            ?.optString("entity_id")
            ?.takeIf { it.isNotBlank() }
        return TranscriptionResult(text, addressee)
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
        if (code !in 200..299) {
            throw IllegalStateException("Core respondeu HTTP $code: $body")
        }
        return body
    }
}

package dev.amora.chibi.voice

import org.json.JSONObject

data class VoiceProfile(
    val id: String,
    val label: String,
    val description: String,
    val speed: Float,
    val silenceScale: Float,
    val numSteps: Int,
    val outputGain: Float,
)

data class EmotionVoiceModulation(
    val enabled: Boolean,
    val speedArousal: Float,
    val speedValence: Float,
    val silenceArousal: Float,
    val silenceValence: Float,
    val gainArousal: Float,
    val gainValence: Float,
    val speedMin: Float,
    val speedMax: Float,
    val silenceMin: Float,
    val silenceMax: Float,
    val gainMin: Float,
    val gainMax: Float,
)

data class VoiceProfileCatalog(
    val schemaVersion: Int,
    val revision: String,
    val backendId: String,
    val modelId: String,
    val language: String,
    val sampleRateHz: Int,
    val defaultSpeakerId: Int,
    val defaultProfileId: String,
    val canonical: Boolean,
    val profiles: List<VoiceProfile>,
    val emotionModulation: EmotionVoiceModulation,
) {
    fun defaultProfile(): VoiceProfile =
        profiles.firstOrNull { it.id == defaultProfileId } ?: profiles.first()

    fun profile(id: String): VoiceProfile? = profiles.firstOrNull { it.id == id }

    companion object {
        fun parse(raw: String): VoiceProfileCatalog {
            val root = JSONObject(raw)
            val schemaVersion = root.getInt("schema_version")
            require(schemaVersion == 1) { "schema de perfil vocal não suportado: $schemaVersion" }

            val backend = root.getJSONObject("backend")
            val lab = root.getJSONObject("lab")
            val profileArray = root.getJSONArray("profiles")
            require(profileArray.length() in 1..32) { "catálogo de perfis vazio ou grande demais" }

            val profiles = buildList(profileArray.length()) {
                for (index in 0 until profileArray.length()) {
                    val item = profileArray.getJSONObject(index)
                    add(
                        VoiceProfile(
                            id = item.getString("id").also { require(it.isNotBlank() && it.length <= 64) },
                            label = item.getString("label").also { require(it.isNotBlank() && it.length <= 96) },
                            description = item.optString("description", "").take(320),
                            speed = item.getDouble("speed").toFloat().also { require(it in 0.7f..1.3f) },
                            silenceScale = item.getDouble("silence_scale").toFloat().also { require(it in 0.5f..1.5f) },
                            numSteps = item.getInt("num_steps").also { require(it in 2..16) },
                            outputGain = item.getDouble("output_gain").toFloat().also { require(it in 0.5f..1.0f) },
                        ),
                    )
                }
            }
            require(profiles.map { it.id }.distinct().size == profiles.size) { "IDs de perfil duplicados" }

            val modulation = root.getJSONObject("emotion_modulation")
            val emotion = EmotionVoiceModulation(
                enabled = modulation.optBoolean("enabled", true),
                speedArousal = modulation.optDouble("speed_arousal", 0.06).toFloat(),
                speedValence = modulation.optDouble("speed_valence", 0.015).toFloat(),
                silenceArousal = modulation.optDouble("silence_arousal", -0.08).toFloat(),
                silenceValence = modulation.optDouble("silence_valence", -0.02).toFloat(),
                gainArousal = modulation.optDouble("gain_arousal", 0.04).toFloat(),
                gainValence = modulation.optDouble("gain_valence", 0.01).toFloat(),
                speedMin = modulation.optDouble("speed_min", 0.85).toFloat(),
                speedMax = modulation.optDouble("speed_max", 1.15).toFloat(),
                silenceMin = modulation.optDouble("silence_min", 0.75).toFloat(),
                silenceMax = modulation.optDouble("silence_max", 1.2).toFloat(),
                gainMin = modulation.optDouble("gain_min", 0.75).toFloat(),
                gainMax = modulation.optDouble("gain_max", 1.0).toFloat(),
            )
            validateEmotion(emotion)

            val defaultProfileId = lab.getString("default_profile_id")
            require(profiles.any { it.id == defaultProfileId }) { "perfil padrão não existe: $defaultProfileId" }

            return VoiceProfileCatalog(
                schemaVersion = schemaVersion,
                revision = root.getString("revision").also { require(it.isNotBlank() && it.length <= 128) },
                backendId = backend.getString("id"),
                modelId = backend.getString("model_id"),
                language = backend.optString("language", "pt"),
                sampleRateHz = backend.optInt("sample_rate_hz", 24_000),
                defaultSpeakerId = lab.optInt("default_speaker_id", 0).also { require(it in 0..255) },
                defaultProfileId = defaultProfileId,
                canonical = lab.optBoolean("canonical", false),
                profiles = profiles,
                emotionModulation = emotion,
            )
        }

        fun builtIn(): VoiceProfileCatalog = parse(BUILTIN_JSON)

        private fun validateEmotion(value: EmotionVoiceModulation) {
            require(value.speedArousal in -0.25f..0.25f)
            require(value.speedValence in -0.25f..0.25f)
            require(value.silenceArousal in -0.4f..0.4f)
            require(value.silenceValence in -0.4f..0.4f)
            require(value.gainArousal in -0.25f..0.25f)
            require(value.gainValence in -0.25f..0.25f)
            require(value.speedMin in 0.7f..1.3f && value.speedMax in 0.7f..1.3f && value.speedMin <= value.speedMax)
            require(value.silenceMin in 0.5f..1.5f && value.silenceMax in 0.5f..1.5f && value.silenceMin <= value.silenceMax)
            require(value.gainMin in 0.5f..1.0f && value.gainMax in 0.5f..1.0f && value.gainMin <= value.gainMax)
        }

        private const val BUILTIN_JSON = """
            {
              "schema_version": 1,
              "revision": "apk-builtin-voice-lab-v1",
              "backend": {
                "id": "supertonic-3-int8",
                "model_id": "sherpa-onnx-supertonic-3-tts-int8-2026-05-11",
                "language": "pt",
                "sample_rate_hz": 24000
              },
              "lab": {
                "default_speaker_id": 0,
                "default_profile_id": "NATURAL",
                "canonical": false
              },
              "profiles": [
                {"id":"NATURAL","label":"NATURAL","description":"Referência limpa.","speed":1.0,"silence_scale":1.0,"num_steps":8,"output_gain":0.92},
                {"id":"AMORA_SOFT","label":"AMORA_SOFT","description":"Mais calma e macia.","speed":0.95,"silence_scale":1.05,"num_steps":8,"output_gain":0.92},
                {"id":"AMORA_SYNTH","label":"AMORA_SYNTH","description":"Fantasia mais seca.","speed":0.92,"silence_scale":0.9,"num_steps":5,"output_gain":0.90},
                {"id":"CHIBI","label":"CHIBI","description":"Fantasia mais rápida.","speed":1.08,"silence_scale":0.9,"num_steps":6,"output_gain":0.88}
              ],
              "emotion_modulation": {
                "enabled": true,
                "speed_arousal": 0.06,
                "speed_valence": 0.015,
                "silence_arousal": -0.08,
                "silence_valence": -0.02,
                "gain_arousal": 0.04,
                "gain_valence": 0.01,
                "speed_min": 0.85,
                "speed_max": 1.15,
                "silence_min": 0.75,
                "silence_max": 1.2,
                "gain_min": 0.75,
                "gain_max": 1.0
              }
            }
        """
    }
}

package dev.amora.chibi.voice

import org.json.JSONObject

data class VoiceProfileConfig(
    val schemaVersion: Int,
    val profileId: String,
    val revision: Int,
    val activeProfile: String,
    val base: Base,
    val synthesis: Synthesis,
    val signature: Signature,
    val prosody: Prosody,
    val profiles: Map<String, Expression>,
) {
    data class Base(val backend: String, val speakerId: Int, val language: String)
    data class Synthesis(val speed: Float, val silenceScale: Float, val numSteps: Int)
    data class Signature(
        val lowShelfDb: Float,
        val presenceDb: Float,
        val airDb: Float,
        val softSaturation: Float,
        val fantasyTexture: Float,
        val compressorThreshold: Float,
        val compressorRatio: Float,
        val compressorAttackMs: Float,
        val compressorReleaseMs: Float,
        val outputGain: Float,
        val limiter: Float,
    )
    data class Prosody(
        val minSpeed: Float,
        val maxSpeed: Float,
        val arousalRateAmount: Float,
        val pitchToPresenceDb: Float,
        val valenceToAirDb: Float,
        val arousalToPresenceDb: Float,
    )
    data class Expression(
        val speedScale: Float,
        val presenceDeltaDb: Float,
        val airDeltaDb: Float,
        val textureScale: Float,
        val saturationScale: Float,
    )

    data class Effective(
        val speed: Float,
        val silenceScale: Float,
        val numSteps: Int,
        val lowShelfDb: Float,
        val presenceDb: Float,
        val airDb: Float,
        val softSaturation: Float,
        val fantasyTexture: Float,
        val compressorThreshold: Float,
        val compressorRatio: Float,
        val compressorAttackMs: Float,
        val compressorReleaseMs: Float,
        val outputGain: Float,
        val limiter: Float,
    )

    fun effective(
        requestedRate: Float,
        requestedPitch: Float,
        valence: Float,
        arousal: Float,
    ): Effective {
        val expression = profiles.getValue(activeProfile)
        val boundedValence = valence.coerceIn(-1f, 1f)
        val boundedArousal = arousal.coerceIn(-1f, 1f)
        // The Core already maps mood arousal into SpeechStyle.rate. The optional
        // coefficient defaults to zero to avoid double-counting emotion; it is
        // retained as a bounded tuning hook for later calibration.
        val emotionRate = 1f + boundedArousal * prosody.arousalRateAmount
        val speed = (synthesis.speed * requestedRate * expression.speedScale * emotionRate)
            .coerceIn(prosody.minSpeed, prosody.maxSpeed)
        val pitchSignal = (requestedPitch.coerceIn(0.9f, 1.1f) - 1f) * 10f
        val presence = (
            signature.presenceDb +
                expression.presenceDeltaDb +
                pitchSignal * prosody.pitchToPresenceDb +
                boundedArousal * prosody.arousalToPresenceDb
            ).coerceIn(-4f, 4f)
        val air = (
            signature.airDb + expression.airDeltaDb + boundedValence * prosody.valenceToAirDb
            ).coerceIn(-4f, 4f)
        return Effective(
            speed = speed,
            silenceScale = synthesis.silenceScale,
            numSteps = synthesis.numSteps,
            lowShelfDb = signature.lowShelfDb,
            presenceDb = presence,
            airDb = air,
            softSaturation = (signature.softSaturation * expression.saturationScale).coerceIn(0f, 0.3f),
            fantasyTexture = (signature.fantasyTexture * expression.textureScale).coerceIn(0f, 0.15f),
            compressorThreshold = signature.compressorThreshold,
            compressorRatio = signature.compressorRatio,
            compressorAttackMs = signature.compressorAttackMs,
            compressorReleaseMs = signature.compressorReleaseMs,
            outputGain = signature.outputGain,
            limiter = signature.limiter,
        )
    }

    fun validateFor(identity: VoiceIdentity): VoiceProfileConfig {
        require(schemaVersion == 1) { "voice profile schema não suportado: $schemaVersion" }
        require(profileId == "amora-voice-v1") { "voice profile id inesperado: $profileId" }
        require(revision >= 1) { "voice profile revision inválida" }
        require(base.backend == identity.backendId) { "backend do profile não corresponde à identidade" }
        require(base.speakerId == identity.speakerId) { "speaker do profile não corresponde à identidade" }
        require(base.language == identity.language) { "idioma do profile não corresponde à identidade" }
        require(activeProfile in profiles) { "active_profile ausente" }
        require(setOf("NATURAL", "SOFT", "SHY", "SYNTH", "CHIBI").all(profiles::containsKey)) {
            "voice profile sem presets obrigatórios"
        }
        require(synthesis.speed in 0.75f..1.25f && synthesis.silenceScale in 0.5f..1.5f && synthesis.numSteps in 2..16)
        require(prosody.minSpeed in 0.7f..1f && prosody.maxSpeed in 1f..1.35f && prosody.minSpeed <= prosody.maxSpeed)
        require(signature.lowShelfDb in -4f..4f && signature.presenceDb in -4f..4f && signature.airDb in -4f..4f)
        require(signature.softSaturation in 0f..0.3f && signature.fantasyTexture in 0f..0.15f)
        require(signature.compressorThreshold in 0.35f..0.95f && signature.compressorRatio in 1f..4f)
        require(signature.compressorAttackMs in 1f..40f && signature.compressorReleaseMs in 20f..300f)
        require(signature.outputGain in 0.5f..1.2f && signature.limiter in 0.75f..1f)
        return this
    }

    companion object {
        fun parse(json: String): VoiceProfileConfig {
            val root = JSONObject(json)
            val base = root.getJSONObject("base")
            val synthesis = root.getJSONObject("synthesis")
            val signature = root.getJSONObject("signature")
            val prosody = root.getJSONObject("prosody")
            val rawProfiles = root.getJSONObject("profiles")
            val parsedProfiles = buildMap {
                val keys = rawProfiles.keys()
                while (keys.hasNext()) {
                    val name = keys.next()
                    val item = rawProfiles.getJSONObject(name)
                    put(name, Expression(
                        speedScale = item.getDouble("speed_scale").toFloat(),
                        presenceDeltaDb = item.getDouble("presence_delta_db").toFloat(),
                        airDeltaDb = item.getDouble("air_delta_db").toFloat(),
                        textureScale = item.getDouble("texture_scale").toFloat(),
                        saturationScale = item.getDouble("saturation_scale").toFloat(),
                    ))
                }
            }
            return VoiceProfileConfig(
                schemaVersion = root.getInt("schema_version"),
                profileId = root.getString("profile_id"),
                revision = root.getInt("revision"),
                activeProfile = root.getString("active_profile"),
                base = Base(base.getString("backend"), base.getInt("speaker_id"), base.getString("language")),
                synthesis = Synthesis(
                    speed = synthesis.getDouble("speed").toFloat(),
                    silenceScale = synthesis.getDouble("silence_scale").toFloat(),
                    numSteps = synthesis.getInt("num_steps"),
                ),
                signature = Signature(
                    lowShelfDb = signature.getDouble("low_shelf_db").toFloat(),
                    presenceDb = signature.getDouble("presence_db").toFloat(),
                    airDb = signature.getDouble("air_db").toFloat(),
                    softSaturation = signature.getDouble("soft_saturation").toFloat(),
                    fantasyTexture = signature.getDouble("fantasy_texture").toFloat(),
                    compressorThreshold = signature.getDouble("compressor_threshold").toFloat(),
                    compressorRatio = signature.getDouble("compressor_ratio").toFloat(),
                    compressorAttackMs = signature.getDouble("compressor_attack_ms").toFloat(),
                    compressorReleaseMs = signature.getDouble("compressor_release_ms").toFloat(),
                    outputGain = signature.getDouble("output_gain").toFloat(),
                    limiter = signature.getDouble("limiter").toFloat(),
                ),
                prosody = Prosody(
                    minSpeed = prosody.getDouble("min_speed").toFloat(),
                    maxSpeed = prosody.getDouble("max_speed").toFloat(),
                    arousalRateAmount = prosody.getDouble("arousal_rate_amount").toFloat(),
                    pitchToPresenceDb = prosody.getDouble("pitch_to_presence_db").toFloat(),
                    valenceToAirDb = prosody.getDouble("valence_to_air_db").toFloat(),
                    arousalToPresenceDb = prosody.getDouble("arousal_to_presence_db").toFloat(),
                ),
                profiles = parsedProfiles,
            )
        }

        fun safeDefault(identity: VoiceIdentity = VoiceIdentity.AMORA_V1): VoiceProfileConfig = parse(DEFAULT_JSON).validateFor(identity)

        private const val DEFAULT_JSON = """{
          "schema_version":1,"profile_id":"amora-voice-v1","revision":1,"active_profile":"NATURAL",
          "base":{"backend":"supertonic3-int8","speaker_id":0,"language":"pt"},
          "synthesis":{"speed":1.0,"silence_scale":1.0,"num_steps":8},
          "signature":{"low_shelf_db":-0.7,"presence_db":1.1,"air_db":0.8,"soft_saturation":0.055,"fantasy_texture":0.025,"compressor_threshold":0.74,"compressor_ratio":1.8,"compressor_attack_ms":5.0,"compressor_release_ms":70.0,"output_gain":0.94,"limiter":0.98},
          "prosody":{"min_speed":0.88,"max_speed":1.12,"arousal_rate_amount":0.0,"pitch_to_presence_db":0.9,"valence_to_air_db":0.35,"arousal_to_presence_db":0.45},
          "profiles":{"NATURAL":{"speed_scale":1.0,"presence_delta_db":0.0,"air_delta_db":0.0,"texture_scale":1.0,"saturation_scale":1.0},"SOFT":{"speed_scale":0.97,"presence_delta_db":-0.35,"air_delta_db":0.35,"texture_scale":0.75,"saturation_scale":0.85},"SHY":{"speed_scale":0.96,"presence_delta_db":-0.5,"air_delta_db":0.45,"texture_scale":0.8,"saturation_scale":0.8},"SYNTH":{"speed_scale":1.0,"presence_delta_db":0.25,"air_delta_db":0.15,"texture_scale":2.2,"saturation_scale":1.25},"CHIBI":{"speed_scale":1.06,"presence_delta_db":0.4,"air_delta_db":0.35,"texture_scale":1.35,"saturation_scale":1.05}}
        }"""
    }
}

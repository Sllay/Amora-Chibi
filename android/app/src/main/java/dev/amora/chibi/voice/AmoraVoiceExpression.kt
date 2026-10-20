package dev.amora.chibi.voice

import kotlin.math.max

/** Android adapter for the frozen AMORA_VOICE_EXPRESSION_V2 contract. */
data class AmoraVoiceExpression(
    val id: String = "AMORA_VOICE_EXPRESSION_V2",
    val rateScale: Float = 1f,
    val pauseScale: Float = 1f,
    val energyScale: Float = 1f,
    val pitchRangeScale: Float = 1f,
    val f0RegisterScale: Float = 1f,
    val softness: Float = 0.25f,
    val firmness: Float = 0.15f,
    val label: String = "NEUTRA",
) {
    companion object {
        val NEUTRAL = AmoraVoiceExpression()
    }
}

private data class EmotionBias(
    val rate: Float = 0f,
    val pause: Float = 0f,
    val energy: Float = 0f,
    val pitchRange: Float = 0f,
    val f0Register: Float = 0f,
    val softness: Float = 0f,
    val firmness: Float = 0f,
)

/**
 * Maps the V/A/D state already owned by Core to provider-independent delivery.
 *
 * This does not decide Amora's emotional state. It only turns Core affect into
 * the approved V2 acoustic controls. A small acoustic-region classifier is used
 * because the current Android utterance contract carries V/A/D but not a
 * discrete EmotionType. If no region is clear, only continuous mood motion is
 * applied. Any failure in callers must fall back to [AmoraVoiceExpression.NEUTRAL].
 */
object AmoraVoiceExpressionPlanner {
    private val JOY = EmotionBias(.030f, -.040f, .025f, .040f, .006f, .055f, 0f)
    private val TENDER = EmotionBias(-.040f, .080f, -.030f, -.015f, -.006f, .250f, -.050f)
    private val ANGER = EmotionBias(.035f, -.070f, .050f, .050f, .006f, -.180f, .420f)
    private val SAD = EmotionBias(-.075f, .140f, -.080f, -.075f, -.030f, .140f, -.120f)

    fun fromVad(valence: Float, arousal: Float, dominance: Float): AmoraVoiceExpression {
        val v = valence.coerceIn(-1f, 1f)
        val a = arousal.coerceIn(0f, 1f)
        val d = dominance.coerceIn(-1f, 1f)

        var rate = 1f + .025f * a + .006f * v
        var pause = 1f - .040f * a - .008f * v
        var energy = 1f + .024f * a + .010f * d
        var pitchRange = 1f + .030f * a
        var register = 1f
        var softness = .25f + .08f * max(v, 0f) - .04f * max(d, 0f)
        var firmness = .15f + .16f * max(d, 0f) + .04f * a

        val classified = when {
            v <= -.25f && a >= .50f && d >= .05f -> Triple("BRAVA", ANGER, max(-v, a).coerceIn(0f, 1f))
            v <= -.25f && a <= .35f -> Triple("TRISTE", SAD, (-v).coerceIn(0f, 1f))
            v >= .30f && a <= .28f -> Triple("CARINHOSA", TENDER, v.coerceIn(0f, 1f))
            v >= .25f && a >= .30f -> Triple("FELIZ", JOY, max(v, a).coerceIn(0f, 1f))
            else -> Triple("NEUTRA", EmotionBias(), 0f)
        }
        val bias = classified.second
        val strength = classified.third
        rate += bias.rate * strength
        pause += bias.pause * strength
        energy += bias.energy * strength
        pitchRange += bias.pitchRange * strength
        register += bias.f0Register * strength
        softness += bias.softness * strength
        firmness += bias.firmness * strength

        return AmoraVoiceExpression(
            rateScale = rate.coerceIn(.90f, 1.08f),
            pauseScale = pause.coerceIn(.88f, 1.16f),
            energyScale = energy.coerceIn(.88f, 1.06f),
            pitchRangeScale = pitchRange.coerceIn(.90f, 1.08f),
            f0RegisterScale = register.coerceIn(.97f, 1.02f),
            softness = softness.coerceIn(0f, 1f),
            firmness = firmness.coerceIn(0f, 1f),
            label = classified.first,
        )
    }

    /** Exact audition presets used for the in-APK physical comparison screen. */
    fun preset(label: String): AmoraVoiceExpression = when (label.uppercase()) {
        "FELIZ" -> applyExplicit(.65f, .45f, .18f, JOY, .75f, "FELIZ")
        "CARINHOSA" -> applyExplicit(.55f, .16f, 0f, TENDER, .70f, "CARINHOSA", extraSoftness = .155f)
        "BRAVA" -> applyExplicit(-.65f, .78f, .55f, ANGER, .90f, "BRAVA", extraSoftness = -.09f, extraFirmness = .18f)
        "TRISTE" -> applyExplicit(-.65f, .10f, -.30f, SAD, .90f, "TRISTE")
        else -> AmoraVoiceExpression.NEUTRAL
    }

    private fun applyExplicit(
        v: Float,
        a: Float,
        d: Float,
        bias: EmotionBias,
        strength: Float,
        label: String,
        extraSoftness: Float = 0f,
        extraFirmness: Float = 0f,
    ): AmoraVoiceExpression {
        val s = strength.coerceIn(0f, 1f)
        return AmoraVoiceExpression(
            rateScale = (1f + .025f * a + .006f * v + bias.rate * s).coerceIn(.90f, 1.08f),
            pauseScale = (1f - .040f * a - .008f * v + bias.pause * s).coerceIn(.88f, 1.16f),
            energyScale = (1f + .024f * a + .010f * d + bias.energy * s).coerceIn(.88f, 1.06f),
            pitchRangeScale = (1f + .030f * a + bias.pitchRange * s).coerceIn(.90f, 1.08f),
            f0RegisterScale = (1f + bias.f0Register * s).coerceIn(.97f, 1.02f),
            softness = (.25f + .08f * max(v, 0f) - .04f * max(d, 0f) + bias.softness * s + extraSoftness).coerceIn(0f, 1f),
            firmness = (.15f + .16f * max(d, 0f) + .04f * a + bias.firmness * s + extraFirmness).coerceIn(0f, 1f),
            label = label,
        )
    }
}

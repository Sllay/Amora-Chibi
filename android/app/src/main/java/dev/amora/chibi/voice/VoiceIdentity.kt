package dev.amora.chibi.voice

/**
 * Canonical local voice identity.
 *
 * BK physically selected Supertonic speaker 0 on Galaxy A15. Server-delivered
 * VoiceProfile data may tune bounded synthesis/DSP parameters, but it is not
 * allowed to silently swap this base speaker/backend.
 */
data class VoiceIdentity(
    val id: String,
    val localeTag: String,
    val packId: String,
    val backendId: String,
    val speakerId: Int,
    val language: String,
    val numThreads: Int,
    val requiredFiles: List<String>,
) {
    companion object {
        val AMORA_V1 = VoiceIdentity(
            id = "amora-voice-v1",
            localeTag = "pt-BR",
            packId = "sherpa-onnx-supertonic-3-tts-int8-2026-05-11",
            backendId = "supertonic3-int8",
            speakerId = 0,
            language = "pt",
            numThreads = 2,
            requiredFiles = listOf(
                "duration_predictor.int8.onnx",
                "text_encoder.int8.onnx",
                "vector_estimator.int8.onnx",
                "vocoder.int8.onnx",
                "tts.json",
                "unicode_indexer.bin",
                "voice.bin",
            ),
        )
    }
}

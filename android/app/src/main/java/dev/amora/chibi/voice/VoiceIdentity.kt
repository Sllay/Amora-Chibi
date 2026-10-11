package dev.amora.chibi.voice

/**
 * Canonical local voice identity.
 *
 * Providers may change and prosody may move a little with Amora's state, but the
 * timbre/locale of normal speech must stay pinned to one local voice pack.
 */
data class VoiceIdentity(
    val id: String,
    val localeTag: String,
    val packId: String,
    val modelFileName: String,
    val tokensFileName: String,
    val dataDirectoryName: String,
    val downloadUrl: String,
    val expectedModelSha256: String,
    val baseSpeed: Float,
    val minSpeed: Float,
    val maxSpeed: Float,
    val outputGain: Float,
    val numThreads: Int,
) {
    fun speedFor(requestedRate: Float): Float =
        (baseSpeed * requestedRate).coerceIn(minSpeed, maxSpeed)

    companion object {
        /**
         * Provisional V1 PT-BR identity used to certify the local pipeline.
         * It is intentionally versioned so the future rights-safe Amora-trained
         * voice can replace the model without changing the voice architecture.
         */
        val AMORA_V1_PROVISIONAL = VoiceIdentity(
            id = "amora-voice-v1-provisional",
            localeTag = "pt-BR",
            packId = "vits-piper-pt_BR-faber-medium",
            modelFileName = "pt_BR-faber-medium.onnx",
            tokensFileName = "tokens.txt",
            dataDirectoryName = "espeak-ng-data",
            downloadUrl = "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-pt_BR-faber-medium.tar.bz2",
            // The sherpa-converted Faber model published by its maintainer.
            expectedModelSha256 = "39fb6b580d6d40a3230b7a9d0851d282074537b9694892b5b3cd90ff87c6cbb3",
            baseSpeed = 1.0f,
            minSpeed = 0.88f,
            maxSpeed = 1.12f,
            outputGain = 1.0f,
            numThreads = 2,
        )
    }
}

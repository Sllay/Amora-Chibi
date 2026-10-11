package dev.amora.chibi.voice

import android.content.Context
import android.util.Log
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream
import org.apache.commons.compress.compressors.bzip2.BZip2CompressorInputStream
import java.io.BufferedInputStream
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/**
 * Installs and verifies one versioned neural voice pack in app-private storage.
 *
 * Downloading is a one-time bootstrap operation. Once the READY marker exists,
 * normal speech is completely local and does not consume a remote TTS quota.
 */
class VoicePackManager(
    context: Context,
    private val identity: VoiceIdentity = VoiceIdentity.AMORA_V1_PROVISIONAL,
) : AutoCloseable {
    enum class State { MISSING, DOWNLOADING, READY, FAILED }

    data class Snapshot(
        val state: State,
        val detail: String,
        val progressPercent: Int? = null,
    )

    private val appContext = context.applicationContext
    private val root = File(appContext.filesDir, "voice_packs")
    private val finalRoot = File(root, identity.packId)
    private val marker = File(finalRoot, READY_MARKER)
    private val executor = Executors.newSingleThreadExecutor { task ->
        Thread(task, "amora-voice-pack").apply { isDaemon = true }
    }
    private val preparing = AtomicBoolean(false)
    private val snapshot = AtomicReference(
        if (isInstalled()) Snapshot(State.READY, identity.id)
        else Snapshot(State.MISSING, "pacote de voz local ainda não instalado"),
    )

    fun state(): Snapshot = snapshot.get()

    fun readyRoot(): File? = if (isInstalled()) finalRoot else null

    fun prepareAsync(onStateChanged: ((Snapshot) -> Unit)? = null) {
        if (isInstalled()) {
            publish(Snapshot(State.READY, identity.id), onStateChanged)
            return
        }
        if (!preparing.compareAndSet(false, true)) return
        executor.execute {
            try {
                install(onStateChanged)
            } catch (error: Throwable) {
                Log.e(TAG, "Falha ao preparar pacote de voz ${identity.packId}", error)
                publish(
                    Snapshot(State.FAILED, error.message?.take(160) ?: error.javaClass.simpleName),
                    onStateChanged,
                )
            } finally {
                preparing.set(false)
            }
        }
    }

    private fun install(onStateChanged: ((Snapshot) -> Unit)?) {
        root.mkdirs()
        val archive = File(root, ".${identity.packId}.tar.bz2.part")
        val staging = File(root, ".${identity.packId}.staging")
        archive.delete()
        staging.deleteRecursively()
        staging.mkdirs()

        publish(Snapshot(State.DOWNLOADING, "baixando voz PT-BR", 0), onStateChanged)
        try {
            download(identity.downloadUrl, archive) { percent ->
                publish(Snapshot(State.DOWNLOADING, "baixando voz PT-BR", percent), onStateChanged)
            }
            extract(archive, staging)

            val model = staging.walkTopDown()
                .firstOrNull { it.isFile && it.name == identity.modelFileName }
                ?: error("modelo ${identity.modelFileName} ausente no pacote")
            val candidateRoot = model.parentFile ?: error("raiz do pacote de voz inválida")
            val tokens = File(candidateRoot, identity.tokensFileName)
            val dataDir = File(candidateRoot, identity.dataDirectoryName)
            require(tokens.isFile) { "tokens.txt ausente no pacote de voz" }
            require(dataDir.isDirectory) { "espeak-ng-data ausente no pacote de voz" }

            val digest = sha256(model)
            require(digest.equals(identity.expectedModelSha256, ignoreCase = true)) {
                "SHA-256 do modelo de voz não confere"
            }

            finalRoot.deleteRecursively()
            require(candidateRoot.renameTo(finalRoot)) { "não foi possível ativar o pacote de voz" }
            marker.writeText(identity.expectedModelSha256 + "\n", Charsets.UTF_8)
            staging.deleteRecursively()
            publish(Snapshot(State.READY, identity.id, 100), onStateChanged)
            Log.i(TAG, "Pacote de voz local pronto: ${identity.id}")
        } finally {
            archive.delete()
            staging.deleteRecursively()
        }
    }

    private fun isInstalled(): Boolean {
        if (!marker.isFile) return false
        val expected = runCatching { marker.readText(Charsets.UTF_8).trim() }.getOrNull() ?: return false
        if (!expected.equals(identity.expectedModelSha256, ignoreCase = true)) return false
        val model = File(finalRoot, identity.modelFileName)
        val tokens = File(finalRoot, identity.tokensFileName)
        val data = File(finalRoot, identity.dataDirectoryName)
        return model.isFile && model.length() > MIN_MODEL_BYTES && tokens.isFile && data.isDirectory
    }

    private fun download(url: String, destination: File, onProgress: (Int) -> Unit) {
        val connection = openFollowingRedirects(url)
        try {
            val expectedLength = connection.contentLengthLong
            if (expectedLength > MAX_ARCHIVE_BYTES) error("pacote de voz excede limite de download")
            BufferedInputStream(connection.inputStream).use { input ->
                FileOutputStream(destination).use { output ->
                    val buffer = ByteArray(COPY_BUFFER_BYTES)
                    var total = 0L
                    var lastPercent = -1
                    while (true) {
                        val count = input.read(buffer)
                        if (count < 0) break
                        total += count
                        if (total > MAX_ARCHIVE_BYTES) error("pacote de voz excede limite de download")
                        output.write(buffer, 0, count)
                        if (expectedLength > 0) {
                            val percent = ((total * 100L) / expectedLength).toInt().coerceIn(0, 99)
                            if (percent >= lastPercent + 5) {
                                lastPercent = percent
                                onProgress(percent)
                            }
                        }
                    }
                    output.fd.sync()
                }
            }
        } finally {
            connection.disconnect()
        }
    }

    private fun openFollowingRedirects(rawUrl: String): HttpURLConnection {
        var current = URL(rawUrl)
        repeat(MAX_REDIRECTS + 1) { attempt ->
            val connection = (current.openConnection() as HttpURLConnection).apply {
                instanceFollowRedirects = false
                connectTimeout = CONNECT_TIMEOUT_MS
                readTimeout = READ_TIMEOUT_MS
                setRequestProperty("User-Agent", "Amora-Chibi/${identity.id}")
            }
            val status = connection.responseCode
            if (status in 300..399) {
                val location = connection.getHeaderField("Location")
                    ?: run {
                        connection.disconnect()
                        error("redirecionamento sem Location")
                    }
                connection.disconnect()
                if (attempt >= MAX_REDIRECTS) error("redirecionamentos demais ao baixar voz")
                current = URL(current, location)
                return@repeat
            }
            if (status !in 200..299) {
                val message = "download da voz falhou HTTP $status"
                connection.disconnect()
                error(message)
            }
            return connection
        }
        error("falha ao seguir download do pacote de voz")
    }

    private fun extract(archive: File, destination: File) {
        var extractedBytes = 0L
        var entries = 0
        val destinationCanonical = destination.canonicalFile
        BZip2CompressorInputStream(BufferedInputStream(FileInputStream(archive))).use { bz2 ->
            TarArchiveInputStream(bz2).use { tar ->
                while (true) {
                    val entry = tar.nextTarEntry ?: break
                    entries += 1
                    if (entries > MAX_ENTRIES) error("pacote de voz possui arquivos demais")
                    if (entry.isSymbolicLink || entry.isLink) error("links não são permitidos no pacote de voz")

                    val output = File(destination, entry.name).canonicalFile
                    val prefix = destinationCanonical.path + File.separator
                    if (output != destinationCanonical && !output.path.startsWith(prefix)) {
                        error("caminho inválido no pacote de voz")
                    }
                    if (entry.isDirectory) {
                        output.mkdirs()
                        continue
                    }
                    if (!entry.isFile) continue
                    output.parentFile?.mkdirs()
                    FileOutputStream(output).use { stream ->
                        val buffer = ByteArray(COPY_BUFFER_BYTES)
                        while (true) {
                            val count = tar.read(buffer)
                            if (count < 0) break
                            extractedBytes += count
                            if (extractedBytes > MAX_EXTRACTED_BYTES) {
                                error("pacote de voz excede limite extraído")
                            }
                            stream.write(buffer, 0, count)
                        }
                    }
                }
            }
        }
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        FileInputStream(file).use { input ->
            val buffer = ByteArray(COPY_BUFFER_BYTES)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                digest.update(buffer, 0, count)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    private fun publish(value: Snapshot, callback: ((Snapshot) -> Unit)?) {
        snapshot.set(value)
        callback?.invoke(value)
    }

    override fun close() {
        executor.shutdownNow()
    }

    companion object {
        private const val TAG = "AmoraVoicePack"
        private const val READY_MARKER = ".amora-voice-ready"
        private const val MIN_MODEL_BYTES = 10L * 1024L * 1024L
        private const val MAX_ARCHIVE_BYTES = 180L * 1024L * 1024L
        private const val MAX_EXTRACTED_BYTES = 260L * 1024L * 1024L
        private const val MAX_ENTRIES = 20_000
        private const val MAX_REDIRECTS = 5
        private const val CONNECT_TIMEOUT_MS = 20_000
        private const val READ_TIMEOUT_MS = 120_000
        private const val COPY_BUFFER_BYTES = 64 * 1024
    }
}

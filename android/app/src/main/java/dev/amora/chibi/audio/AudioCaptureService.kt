package dev.amora.chibi.audio

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AutomaticGainControl
import android.media.audiofx.NoiseSuppressor
import android.os.IBinder
import android.util.Log
import dev.amora.chibi.R
import dev.amora.chibi.core.CoreClient
import dev.amora.chibi.voice.VoiceDiagnostics
import dev.amora.chibi.voice.VoiceOutput
import java.util.ArrayDeque
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

class AudioCaptureService : Service() {
    private val running = AtomicBoolean(false)
    private val speaking = AtomicBoolean(false)
    // voiceActive covers both neural preparation and actual AudioTrack playback.
    // Barge-in must work in both states; otherwise BK cannot cancel a slow render.
    private val voiceActive = AtomicBoolean(false)
    private val voiceBusy = AtomicBoolean(false)
    private val prefetchBusy = AtomicBoolean(false)
    private val resetSegmenter = AtomicBoolean(false)
    private val prefetchedItem = AtomicReference<CoreClient.SpeechUtterance?>(null)

    private val networkExecutor = Executors.newSingleThreadExecutor()
    private val controlExecutor = Executors.newSingleThreadExecutor()
    private val voicePollExecutor = Executors.newSingleThreadScheduledExecutor()
    private val voicePlaybackExecutor = Executors.newSingleThreadExecutor()
    private val voicePrefetchExecutor = Executors.newSingleThreadExecutor()
    private val bargeIn = BargeInDetector()

    private var captureThread: Thread? = null
    private var recorder: AudioRecord? = null
    private var segmenter: SpeechSegmenter? = null
    private var voiceOutput: VoiceOutput? = null
    @Volatile private var voiceStatus = "voz neural: iniciando"
    private lateinit var coreClient: CoreClient

    override fun onBind(intent: android.content.Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createChannel()
        coreClient = CoreClient(this)
        voiceOutput = VoiceOutput(
            this,
            onTurnActiveChanged = { active ->
                voiceActive.set(active)
                resetSegmenter.set(true)
                if (active) bargeIn.speechStarted() else bargeIn.reset()
                notifyVoiceState(statusPrefix() + " · $voiceStatus")
            },
            onSpeakingChanged = { active ->
                speaking.set(active)
                notifyVoiceState(statusPrefix() + " · $voiceStatus")
            },
            onAudioStarted = { utteranceId ->
                // A reserved/prefetched item becomes SPEAKING only when samples
                // actually begin playback, keeping Core diagnostics truthful.
                controlExecutor.execute {
                    runCatching { coreClient.markUtteranceStarted(utteranceId) }
                        .onFailure { error -> Log.w(TAG, "Falha ao marcar início de fala $utteranceId", error) }
                }
                voiceOutput?.currentUtterance()?.let(::schedulePrefetch)
            },
            onFinished = { utteranceId, outcome, spokenChars ->
                if (outcome == VoiceOutput.Outcome.SPOKEN) {
                    // Do not put localhost ACK latency between two spoken segments.
                    // The next already-rendered segment may begin while ACK is sent.
                    voiceBusy.set(false)
                    kickVoicePoll()
                    networkExecutor.execute {
                        runCatching { coreClient.acknowledgeUtterance(utteranceId, true) }
                            .onFailure { error -> Log.w(TAG, "Falha ao confirmar fala $utteranceId", error) }
                    }
                } else {
                    // On interruption/failure, cancel the complete cognitive turn
                    // before allowing another claim so old story segments cannot
                    // resume after BK said "para".
                    prefetchedItem.set(null)
                    voiceOutput?.discardPrepared()
                    val executor = when (outcome) {
                        VoiceOutput.Outcome.INTERRUPTED,
                        VoiceOutput.Outcome.PARTIAL -> controlExecutor
                        else -> networkExecutor
                    }
                    executor.execute {
                        try {
                            when (outcome) {
                                VoiceOutput.Outcome.FAILED -> coreClient.cancelUtterance(utteranceId, "local_voice_failure", null)
                                VoiceOutput.Outcome.INTERRUPTED -> coreClient.cancelUtterance(utteranceId, "barge_in", spokenChars)
                                VoiceOutput.Outcome.PARTIAL -> coreClient.cancelUtterance(utteranceId, "tts_partial_failure", spokenChars)
                                VoiceOutput.Outcome.SPOKEN -> Unit
                            }
                        } catch (error: Throwable) {
                            Log.w(TAG, "Falha ao finalizar fala $utteranceId outcome=$outcome", error)
                        } finally {
                            voiceBusy.set(false)
                            kickVoicePoll()
                        }
                    }
                }
            },
            onVoiceStatusChanged = { state ->
                voiceStatus = "${state.stage}: ${state.detail}".take(220)
                notifyVoiceState(statusPrefix() + " · $voiceStatus")
            },
        )
        voicePollExecutor.scheduleWithFixedDelay(::pollVoice, 250, 250, TimeUnit.MILLISECONDS)
    }

    override fun onStartCommand(intent: android.content.Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        startForeground(NOTIFICATION_ID, notification("Amora está ouvindo · $voiceStatus"))
        if (running.compareAndSet(false, true)) {
            captureThread = Thread(::captureLoop, "amora-audio-capture").apply { start() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        running.set(false)
        speaking.set(false)
        voiceActive.set(false)
        prefetchedItem.set(null)
        runCatching { voiceOutput?.stopCurrent() }
        runCatching { recorder?.stop() }
        recorder?.release()
        recorder = null
        captureThread?.interrupt()
        captureThread = null
        segmenter?.close()
        segmenter = null
        voicePollExecutor.shutdownNow()
        voicePrefetchExecutor.shutdownNow()
        voicePlaybackExecutor.shutdownNow()
        voiceOutput?.close()
        voiceOutput = null
        networkExecutor.shutdownNow()
        controlExecutor.shutdownNow()
        super.onDestroy()
    }

    private fun statusPrefix(): String = when {
        speaking.get() -> "Amora está falando"
        voiceActive.get() -> "Amora está preparando a fala"
        else -> "Amora está ouvindo"
    }

    private fun kickVoicePoll() {
        if (!running.get() || voicePollExecutor.isShutdown) return
        runCatching { voicePollExecutor.execute(::pollVoice) }
    }

    private fun pollVoice() {
        if (!running.get() || !coreClient.isPaired() || voiceBusy.get()) return

        val prefetched = prefetchedItem.get()
        if (prefetched != null && voiceOutput?.isPrepared(prefetched.id) == true) {
            if (prefetchedItem.compareAndSet(prefetched, null)) startPlayback(prefetched)
            return
        }
        if (prefetchBusy.get()) return

        val item = try { coreClient.nextUtterance() } catch (_: Throwable) { return } ?: return
        startPlayback(item)
    }

    private fun startPlayback(item: CoreClient.SpeechUtterance) {
        if (!voiceBusy.compareAndSet(false, true)) return
        voicePlaybackExecutor.execute {
            val accepted = runCatching { voiceOutput?.speak(item) == true }
                .getOrElse { error ->
                    Log.w(TAG, "Falha ao iniciar voz ${item.id}", error)
                    false
                }
            if (!accepted) {
                runCatching { coreClient.cancelUtterance(item.id, "voice_output_rejected", null) }
                    .onFailure { error -> Log.w(TAG, "Falha ao cancelar fala rejeitada ${item.id}", error) }
                voiceBusy.set(false)
                kickVoicePoll()
            }
        }
    }

    private fun schedulePrefetch(current: CoreClient.SpeechUtterance) {
        val turnId = current.cognitiveTurnId ?: return
        if (!running.get() || prefetchedItem.get() != null) return
        if (!prefetchBusy.compareAndSet(false, true)) return

        voicePrefetchExecutor.execute {
            var next: CoreClient.SpeechUtterance? = null
            try {
                next = coreClient.nextUtterance(cognitiveTurnId = turnId) ?: return@execute
                // The Core filter is authoritative, but keep a local defense so a
                // future server regression cannot pre-render a different turn.
                if (next.cognitiveTurnId != turnId) {
                    Log.w(TAG, "Prefetch rejeitado: turno ${next.cognitiveTurnId} != $turnId")
                    return@execute
                }
                if (voiceOutput?.prefetch(next) == true) {
                    prefetchedItem.set(next)
                    Log.i(TAG, "Segmento ${next.id} pré-renderizado durante playback")
                } else if (voiceActive.get()) {
                    // A genuine prefetch failure while the current speech remains
                    // active invalidates this response turn. Barge-in is handled by
                    // the current utterance cancellation path instead.
                    runCatching { coreClient.cancelUtterance(next.id, "local_voice_prefetch_failure", null) }
                        .onFailure { error -> Log.w(TAG, "Falha ao cancelar prefetch ${next.id}", error) }
                }
            } catch (error: Throwable) {
                Log.w(TAG, "Falha no prefetch do turno $turnId", error)
                next?.let { item ->
                    if (voiceActive.get()) {
                        runCatching { coreClient.cancelUtterance(item.id, "local_voice_prefetch_failure", null) }
                    }
                }
            } finally {
                prefetchBusy.set(false)
                kickVoicePoll()
            }
        }
    }

    private fun captureLoop() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            Log.e(TAG, "RECORD_AUDIO não concedido")
            stopSelf()
            return
        }

        val sampleRate = 16_000
        val minBytes = AudioRecord.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val bufferBytes = maxOf(minBytes, 4096)
        val localRecorder = AudioRecord(
            MediaRecorder.AudioSource.VOICE_COMMUNICATION,
            sampleRate,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            bufferBytes,
        )
        recorder = localRecorder
        val echoCanceler = if (AcousticEchoCanceler.isAvailable()) {
            runCatching { AcousticEchoCanceler.create(localRecorder.audioSessionId) }
                .getOrNull()
                ?.also { effect ->
                    effect.enabled = true
                    Log.i(TAG, "AcousticEchoCanceler ativo=${effect.enabled}")
                }
        } else {
            Log.w(TAG, "AcousticEchoCanceler indisponível; barge-in ficará conservador")
            null
        }
        val noiseSuppressor = if (NoiseSuppressor.isAvailable()) {
            runCatching { NoiseSuppressor.create(localRecorder.audioSessionId) }
                .getOrNull()
                ?.also { effect ->
                    effect.enabled = true
                    Log.i(TAG, "NoiseSuppressor ativo=${effect.enabled}")
                }
        } else {
            Log.w(TAG, "NoiseSuppressor indisponível")
            null
        }
        val automaticGain = if (AutomaticGainControl.isAvailable()) {
            runCatching { AutomaticGainControl.create(localRecorder.audioSessionId) }
                .getOrNull()
                ?.also { effect ->
                    // VOICE_COMMUNICATION already receives device processing. AGC
                    // stays off to avoid pumping wind/room noise into false speech.
                    effect.enabled = false
                    Log.i(TAG, "AutomaticGainControl ativo=${effect.enabled}")
                }
        } else null

        var localSegmenter = SpeechSegmenterFactory.create(this)
        segmenter = localSegmenter
        val shorts = ShortArray(bufferBytes / 2)
        val bargeInPreRoll = ArrayDeque<FloatArray>()
        var preRollSamples = 0
        val maxPreRollSamples = sampleRate * BARGE_IN_PREROLL_MS / 1000

        fun clearPreRoll() {
            bargeInPreRoll.clear()
            preRollSamples = 0
        }

        fun appendPreRoll(chunk: FloatArray) {
            bargeInPreRoll.addLast(chunk.copyOf())
            preRollSamples += chunk.size
            while (preRollSamples > maxPreRollSamples && bargeInPreRoll.size > 1) {
                preRollSamples -= bargeInPreRoll.removeFirst().size
            }
        }

        try {
            localRecorder.startRecording()
            while (running.get()) {
                val count = localRecorder.read(shorts, 0, shorts.size)
                if (count <= 0) continue

                if (resetSegmenter.compareAndSet(true, false)) {
                    runCatching { localSegmenter.close() }
                    localSegmenter = SpeechSegmenterFactory.create(this)
                    segmenter = localSegmenter
                    if (!voiceActive.get()) clearPreRoll()
                }

                val floats = FloatArray(count) { index -> shorts[index] / 32768F }
                if (voiceActive.get()) {
                    appendPreRoll(floats)
                    if (bargeIn.accept(floats)) {
                        Log.i(TAG, "Barge-in detectado; cancelando preparo/playback e o restante do turno")
                        prefetchedItem.set(null)
                        voiceOutput?.discardPrepared()
                        voiceOutput?.stopCurrent()

                        runCatching { localSegmenter.close() }
                        localSegmenter = SpeechSegmenterFactory.create(this)
                        segmenter = localSegmenter
                        resetSegmenter.set(false)

                        if (!voiceActive.get()) {
                            val recovered = FloatArray(preRollSamples)
                            var offset = 0
                            for (chunk in bargeInPreRoll) {
                                chunk.copyInto(recovered, destinationOffset = offset)
                                offset += chunk.size
                            }
                            clearPreRoll()
                            for (segment in localSegmenter.accept(recovered)) submitSegment(segment)
                        }
                    }
                    continue
                }

                clearPreRoll()
                for (segment in localSegmenter.accept(floats)) submitSegment(segment)
            }
            if (!voiceActive.get()) {
                for (segment in localSegmenter.flush()) submitSegment(segment)
            }
        } catch (error: Throwable) {
            Log.e(TAG, "Falha no loop de captura", error)
        } finally {
            runCatching { localRecorder.stop() }
            runCatching { echoCanceler?.release() }
            runCatching { noiseSuppressor?.release() }
            runCatching { automaticGain?.release() }
            localRecorder.release()
            recorder = null
            localSegmenter.close()
            segmenter = null
        }
    }

    private fun submitSegment(segment: FloatArray) {
        if (!coreClient.isPaired() || voiceActive.get()) return
        val wav = WavEncoder.pcm16(segment)
        networkExecutor.execute {
            runCatching { coreClient.transcribe(wav) }
                .onSuccess { result ->
                    if (!result.accepted) {
                        Log.d(TAG, "STT rejeitou segmento sem evidência suficiente de fala")
                        return@onSuccess
                    }
                    val target = result.addresseeId ?: "addressee incerto"
                    Log.i(TAG, "STT: ${result.text} [$target] replyQueued=${result.replyQueued}")
                    notifyVoiceState(result.text.ifBlank { "Fala detectada" })
                }
                .onFailure { error -> Log.w(TAG, "Falha ao enviar segmento ao Core", error) }
        }
    }

    private fun notifyVoiceState(text: String) {
        getSystemService(NotificationManager::class.java)
            .notify(NOTIFICATION_ID, notification(text))
    }

    private fun createChannel() {
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Amora — microfone e voz", NotificationManager.IMPORTANCE_LOW),
        )
    }

    private fun notification(text: String): Notification = Notification.Builder(this, CHANNEL_ID)
        .setContentTitle("Amora")
        .setContentText(text.take(120))
        .setStyle(Notification.BigTextStyle().bigText(text.take(320)))
        .setSmallIcon(R.drawable.ic_amora_notification)
        .setOngoing(true)
        .build()

    companion object {
        const val ACTION_STOP = "dev.amora.chibi.action.STOP_LISTENING"
        private const val CHANNEL_ID = "amora_microphone"
        private const val NOTIFICATION_ID = 2001
        private const val BARGE_IN_PREROLL_MS = 420
        private const val TAG = "AmoraAudio"
    }
}

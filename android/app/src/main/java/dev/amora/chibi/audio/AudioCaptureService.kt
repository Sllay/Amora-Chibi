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
import android.os.IBinder
import android.util.Log
import dev.amora.chibi.core.CoreClient
import dev.amora.chibi.voice.VoiceOutput
import java.util.ArrayDeque
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

class AudioCaptureService : Service() {
    private val running = AtomicBoolean(false)
    private val speaking = AtomicBoolean(false)
    private val voiceBusy = AtomicBoolean(false)
    private val resetSegmenter = AtomicBoolean(false)
    private val networkExecutor = Executors.newSingleThreadExecutor()
    // Control traffic must never wait behind a long STT/cognition request. In
    // particular, barge-in cancellation has to reach the Core immediately.
    private val controlExecutor = Executors.newSingleThreadExecutor()
    private val voiceExecutor = Executors.newSingleThreadScheduledExecutor()
    private val bargeIn = BargeInDetector()
    private var captureThread: Thread? = null
    private var recorder: AudioRecord? = null
    private var segmenter: SpeechSegmenter? = null
    private var voiceOutput: VoiceOutput? = null
    private lateinit var coreClient: CoreClient

    override fun onBind(intent: android.content.Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createChannel()
        coreClient = CoreClient(this)
        voiceOutput = VoiceOutput(
            this,
            onSpeakingChanged = { active ->
                speaking.set(active)
                resetSegmenter.set(true)
                if (active) bargeIn.speechStarted() else bargeIn.reset()
                val manager = getSystemService(NotificationManager::class.java)
                val text = if (active) "Amora está falando" else "Amora está ouvindo localmente"
                manager.notify(NOTIFICATION_ID, notification(text))
            },
            onFinished = { utteranceId, outcome, spokenChars ->
                val executor = when (outcome) {
                    VoiceOutput.Outcome.INTERRUPTED,
                    VoiceOutput.Outcome.PARTIAL -> controlExecutor
                    else -> networkExecutor
                }
                executor.execute {
                    try {
                        when (outcome) {
                            VoiceOutput.Outcome.SPOKEN -> coreClient.acknowledgeUtterance(utteranceId, true)
                            VoiceOutput.Outcome.FAILED -> coreClient.acknowledgeUtterance(utteranceId, false)
                            VoiceOutput.Outcome.INTERRUPTED -> coreClient.cancelUtterance(utteranceId, "barge_in", spokenChars)
                            VoiceOutput.Outcome.PARTIAL -> coreClient.cancelUtterance(utteranceId, "tts_partial_failure", spokenChars)
                        }
                    } catch (error: Throwable) {
                        Log.w(TAG, "Falha ao finalizar fala $utteranceId outcome=$outcome", error)
                    } finally {
                        voiceBusy.set(false)
                    }
                }
            },
        )
        voiceExecutor.scheduleWithFixedDelay(::pollVoice, 350, 450, TimeUnit.MILLISECONDS)
    }

    override fun onStartCommand(intent: android.content.Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        startForeground(NOTIFICATION_ID, notification("Amora está ouvindo localmente"))
        if (running.compareAndSet(false, true)) {
            captureThread = Thread(::captureLoop, "amora-audio-capture").apply { start() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        running.set(false)
        speaking.set(false)
        runCatching { voiceOutput?.stopCurrent() }
        runCatching { recorder?.stop() }
        recorder?.release()
        recorder = null
        captureThread?.interrupt()
        captureThread = null
        segmenter?.close()
        segmenter = null
        voiceExecutor.shutdownNow()
        voiceOutput?.close()
        voiceOutput = null
        networkExecutor.shutdownNow()
        controlExecutor.shutdownNow()
        super.onDestroy()
    }

    private fun pollVoice() {
        if (!running.get() || !coreClient.isPaired() || voiceBusy.get()) return
        val item = try { coreClient.nextUtterance() } catch (_: Throwable) { return } ?: return
        if (!voiceBusy.compareAndSet(false, true)) return
        val accepted = runCatching { voiceOutput?.speak(item) == true }
            .getOrElse { error ->
                Log.w(TAG, "Falha ao iniciar voz ${item.id}", error)
                false
            }
        if (!accepted) {
            runCatching { coreClient.acknowledgeUtterance(item.id, false) }
                .onFailure { error -> Log.w(TAG, "Falha ao devolver fala ${item.id} à fila", error) }
            voiceBusy.set(false)
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
                    if (!speaking.get()) clearPreRoll()
                }

                val floats = FloatArray(count) { index -> shorts[index] / 32768F }
                if (speaking.get()) {
                    // Keep only a short echo-cancelled tail. At the instant barge-in
                    // latches, the last ~320 ms contain the sustained human speech
                    // that proved the floor takeover. Feeding that tail after TTS
                    // stops prevents swallowing the beginning of "Mas Amora...".
                    appendPreRoll(floats)
                    if (bargeIn.accept(floats)) {
                        Log.i(TAG, "Barge-in detectado; Amora cede o turno")
                        voiceOutput?.stopCurrent()
                        runCatching { localSegmenter.close() }
                        localSegmenter = SpeechSegmenterFactory.create(this)
                        segmenter = localSegmenter
                        resetSegmenter.set(false)

                        if (!speaking.get()) {
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
            if (!speaking.get()) {
                for (segment in localSegmenter.flush()) submitSegment(segment)
            }
        } catch (error: Throwable) {
            Log.e(TAG, "Falha no loop de captura", error)
        } finally {
            runCatching { localRecorder.stop() }
            runCatching { echoCanceler?.release() }
            localRecorder.release()
            recorder = null
            localSegmenter.close()
            segmenter = null
        }
    }

    private fun submitSegment(segment: FloatArray) {
        if (!coreClient.isPaired() || speaking.get()) return
        val wav = WavEncoder.pcm16(segment)
        networkExecutor.execute {
            runCatching { coreClient.transcribe(wav) }
                .onSuccess { result ->
                    val target = result.addresseeId ?: "addressee incerto"
                    Log.i(TAG, "STT: ${result.text} [$target] replyQueued=${result.replyQueued}")
                    getSystemService(NotificationManager::class.java)
                        .notify(NOTIFICATION_ID, notification(result.text.ifBlank { "Fala detectada" }))
                }
                .onFailure { error -> Log.w(TAG, "Falha ao enviar segmento ao Core", error) }
        }
    }

    private fun createChannel() {
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Amora — microfone e voz", NotificationManager.IMPORTANCE_LOW),
        )
    }

    private fun notification(text: String): Notification = Notification.Builder(this, CHANNEL_ID)
        .setContentTitle("Amora Chibi")
        .setContentText(text.take(120))
        .setSmallIcon(android.R.drawable.ic_btn_speak_now)
        .setOngoing(true)
        .build()

    companion object {
        const val ACTION_STOP = "dev.amora.chibi.action.STOP_LISTENING"
        private const val CHANNEL_ID = "amora_microphone"
        private const val NOTIFICATION_ID = 2001
        private const val BARGE_IN_PREROLL_MS = 320
        private const val TAG = "AmoraAudio"
    }
}

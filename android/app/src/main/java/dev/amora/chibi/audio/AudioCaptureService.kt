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
import android.os.IBinder
import android.util.Log
import dev.amora.chibi.core.CoreClient
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

class AudioCaptureService : Service() {
    private val running = AtomicBoolean(false)
    private val networkExecutor = Executors.newSingleThreadExecutor()
    private var captureThread: Thread? = null
    private var recorder: AudioRecord? = null
    private var segmenter: SpeechSegmenter? = null

    override fun onBind(intent: android.content.Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createChannel()
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
        runCatching { recorder?.stop() }
        recorder?.release()
        recorder = null
        captureThread?.interrupt()
        captureThread = null
        segmenter?.close()
        segmenter = null
        networkExecutor.shutdownNow()
        super.onDestroy()
    }

    private fun captureLoop() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            Log.e(TAG, "RECORD_AUDIO não concedido")
            stopSelf()
            return
        }

        val sampleRate = 16_000
        val minBytes = AudioRecord.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        val bufferBytes = maxOf(minBytes, 4096)
        val localRecorder = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            sampleRate,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            bufferBytes,
        )
        recorder = localRecorder
        val localSegmenter = SpeechSegmenterFactory.create(this)
        segmenter = localSegmenter
        val shorts = ShortArray(bufferBytes / 2)
        val client = CoreClient(this)

        try {
            localRecorder.startRecording()
            while (running.get()) {
                val count = localRecorder.read(shorts, 0, shorts.size)
                if (count <= 0) continue
                val floats = FloatArray(count) { index -> shorts[index] / 32768F }
                for (segment in localSegmenter.accept(floats)) {
                    submitSegment(client, segment)
                }
            }
            for (segment in localSegmenter.flush()) {
                submitSegment(client, segment)
            }
        } catch (error: Throwable) {
            Log.e(TAG, "Falha no loop de captura", error)
        } finally {
            runCatching { localRecorder.stop() }
            localRecorder.release()
            recorder = null
            localSegmenter.close()
            segmenter = null
        }
    }

    private fun submitSegment(client: CoreClient, segment: FloatArray) {
        if (!client.isPaired()) return
        val wav = WavEncoder.pcm16(segment)
        networkExecutor.execute {
            runCatching { client.transcribe(wav) }
                .onSuccess { result ->
                    val target = result.addresseeId ?: "addressee incerto"
                    Log.i(TAG, "STT: ${result.text} [$target]")
                    val manager = getSystemService(NotificationManager::class.java)
                    manager.notify(NOTIFICATION_ID, notification(result.text.ifBlank { "Fala detectada" }))
                }
                .onFailure { error -> Log.w(TAG, "Falha ao enviar segmento ao Core", error) }
        }
    }

    private fun createChannel() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Amora — microfone", NotificationManager.IMPORTANCE_LOW),
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
        private const val TAG = "AmoraAudio"
    }
}

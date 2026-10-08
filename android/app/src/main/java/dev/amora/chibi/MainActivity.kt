package dev.amora.chibi

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.text.InputFilter
import android.text.InputType
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import dev.amora.chibi.audio.AudioCaptureService
import dev.amora.chibi.core.CoreClient

class MainActivity : Activity() {
    private lateinit var statusView: TextView
    private lateinit var codeInput: EditText
    private lateinit var client: CoreClient

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        client = CoreClient(this)
        setContentView(buildUi())
        refreshStatus()
    }

    private fun buildUi(): ScrollView {
        val density = resources.displayMetrics.density
        val padding = (20 * density).toInt()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(padding, padding, padding, padding)
        }

        root.addView(TextView(this).apply {
            text = "Amora Chibi — V0.2 Audio Bridge"
            textSize = 22F
        })
        statusView = TextView(this).also {
            it.setPadding(0, padding / 2, 0, padding)
            root.addView(it)
        }

        codeInput = EditText(this).apply {
            hint = "Código de pareamento (6 dígitos)"
            inputType = InputType.TYPE_CLASS_NUMBER
            filters = arrayOf(InputFilter.LengthFilter(6))
        }
        root.addView(codeInput, ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)

        root.addView(Button(this).apply {
            text = "Parear com Core"
            setOnClickListener { pair() }
        })
        root.addView(Button(this).apply {
            text = "Testar Core"
            setOnClickListener { testCore() }
        })
        root.addView(Button(this).apply {
            text = "Começar a ouvir"
            setOnClickListener { startListening() }
        })
        root.addView(Button(this).apply {
            text = "Parar de ouvir"
            setOnClickListener { stopListening() }
        })

        return ScrollView(this).apply { addView(root) }
    }

    private fun pair() {
        val code = codeInput.text.toString().trim()
        if (!code.matches(Regex("\\d{6}"))) {
            setStatus("Digite os 6 dígitos mostrados por `amora pair` no Termux.")
            return
        }
        setStatus("Pareando…")
        Thread {
            runCatching { client.pair(code) }
                .onSuccess { runOnUiThread { setStatus("Pareada com o Core ✅") } }
                .onFailure { error -> runOnUiThread { setStatus("Falha no pareamento: ${error.message}") } }
        }.start()
    }

    private fun testCore() {
        setStatus("Consultando 127.0.0.1:8765…")
        Thread {
            runCatching { client.health() }
                .onSuccess { health -> runOnUiThread { setStatus("Core: $health") } }
                .onFailure { error -> runOnUiThread { setStatus("Core indisponível: ${error.message}") } }
        }.start()
    }

    private fun startListening() {
        if (!client.isPaired()) {
            setStatus("Pareie o app com o Core primeiro.")
            return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            val permissions = mutableListOf(Manifest.permission.RECORD_AUDIO)
            if (android.os.Build.VERSION.SDK_INT >= 33) permissions += Manifest.permission.POST_NOTIFICATIONS
            requestPermissions(permissions.toTypedArray(), REQUEST_AUDIO)
            return
        }
        startForegroundService(Intent(this, AudioCaptureService::class.java))
        setStatus("Microfone ativo; VAD local filtrando fala.")
    }

    private fun stopListening() {
        val intent = Intent(this, AudioCaptureService::class.java).setAction(AudioCaptureService.ACTION_STOP)
        startService(intent)
        setStatus("Microfone parado.")
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQUEST_AUDIO && grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            startListening()
        } else if (requestCode == REQUEST_AUDIO) {
            setStatus("Permissão de microfone negada.")
        }
    }

    private fun refreshStatus() {
        setStatus(if (client.isPaired()) "Pareada anteriormente ✅" else "Ainda não pareada com o Core.")
    }

    private fun setStatus(value: String) {
        statusView.text = value
    }

    companion object {
        private const val REQUEST_AUDIO = 41
    }
}

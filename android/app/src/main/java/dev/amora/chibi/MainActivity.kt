package dev.amora.chibi

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.os.Bundle
import android.text.InputFilter
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import dev.amora.chibi.audio.AudioCaptureService
import dev.amora.chibi.core.CoreClient
import dev.amora.chibi.voice.VoiceDiagnostics

class MainActivity : Activity() {
    private lateinit var statusView: TextView
    private lateinit var voiceDiagnosticView: TextView
    private lateinit var codeInput: EditText
    private lateinit var pairingContainer: LinearLayout
    private lateinit var pairingToggleButton: Button
    private lateinit var remoteCognitionCheck: CheckBox
    private lateinit var client: CoreClient
    private lateinit var voiceDiagnostics: VoiceDiagnostics
    private var pairingExpanded = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        client = CoreClient(this)
        voiceDiagnostics = VoiceDiagnostics(this)
        setContentView(buildUi())
        refreshPairingUi()
        refreshStatus()
        refreshVoiceDiagnostics()
    }

    override fun onResume() {
        super.onResume()
        if (::voiceDiagnosticView.isInitialized) refreshVoiceDiagnostics()
    }

    private fun buildUi(): ScrollView {
        val scroll = ScrollView(this).apply {
            isFillViewport = true
            setBackgroundResource(R.drawable.bg_amora_gradient)
        }
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(24), dp(20), dp(24))
        }
        scroll.addView(root, ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)

        root.addView(buildHeader())
        root.addView(buildStatusCard(), sectionParams())
        root.addView(buildPairingCard(), sectionParams())
        root.addView(buildVoiceCard(), sectionParams())
        root.addView(buildPrivacyCard(), sectionParams())
        root.addView(TextView(this).apply {
            text = "Amora ${BuildConfig.VERSION_NAME}  •  Core local + linguagem substituível"
            textSize = 12F
            setTextColor(getColor(R.color.amora_text_muted))
            gravity = Gravity.CENTER
            setPadding(0, dp(22), 0, dp(6))
        })

        return scroll
    }

    private fun buildHeader(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL

        addView(ImageView(this@MainActivity).apply {
            setImageResource(R.mipmap.ic_launcher)
            contentDescription = "Ícone da Amora"
        }, LinearLayout.LayoutParams(dp(64), dp(64)))

        addView(LinearLayout(this@MainActivity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(14), 0, 0, 0)
            addView(TextView(this@MainActivity).apply {
                text = "Amora"
                textSize = 28F
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(getColor(R.color.amora_white))
            })
            addView(TextView(this@MainActivity).apply {
                text = "V1 • Cognitive Core"
                textSize = 14F
                setTextColor(getColor(R.color.amora_pink))
            })
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1F))
    }

    private fun buildStatusCard(): LinearLayout = card().apply {
        addView(sectionLabel("CORE / STATUS"))
        statusView = TextView(this@MainActivity).also {
            it.textSize = 16F
            it.setTextColor(getColor(R.color.amora_white))
            it.setPadding(0, dp(8), 0, dp(12))
            addView(it)
        }
        addView(actionButton("Testar conexão com Core", primary = false).apply {
            setOnClickListener { testCore() }
        })
    }

    private fun buildPairingCard(): LinearLayout = card().apply {
        addView(sectionLabel("PAREAMENTO"))
        addView(bodyText("A credencial fica criptografada no Android Keystore e continua salva quando o app é atualizado por cima."))

        pairingContainer = LinearLayout(this@MainActivity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, dp(12), 0, 0)

            codeInput = EditText(this@MainActivity).apply {
                hint = "Código de 6 dígitos"
                inputType = InputType.TYPE_CLASS_NUMBER
                filters = arrayOf(InputFilter.LengthFilter(6))
                setTextColor(getColor(R.color.amora_white))
                setHintTextColor(getColor(R.color.amora_text_muted))
                textSize = 16F
                setBackgroundResource(R.drawable.bg_amora_input)
                setPadding(dp(14), dp(12), dp(14), dp(12))
            }
            addView(codeInput, ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)

            addView(actionButton("Parear com Core", primary = true).apply {
                setOnClickListener { pair() }
            }, sectionParams(topDp = 10))
        }
        addView(pairingContainer)

        pairingToggleButton = actionButton("Trocar pareamento", primary = false).apply {
            setOnClickListener {
                pairingExpanded = !pairingExpanded
                refreshPairingUi()
            }
        }
        addView(pairingToggleButton, sectionParams(topDp = 10))
    }

    private fun buildVoiceCard(): LinearLayout = card().apply {
        addView(sectionLabel("CONVERSA / VOZ LOCAL"))
        addView(bodyText("VAD local, interrupção por voz e sherpa-onnx offline. A voz canônica usa Supertonic speaker 0 + Amora VoiceSignature; o perfil de tuning vem do Core e pode atualizar sem recompilar o APK."))

        voiceDiagnosticView = TextView(this@MainActivity).also {
            it.textSize = 13F
            it.setTextColor(getColor(R.color.amora_white))
            it.setPadding(0, dp(12), 0, dp(4))
            it.setLineSpacing(0F, 1.12F)
            addView(it)
        }

        addView(actionButton("Atualizar diagnóstico da voz", primary = false).apply {
            setOnClickListener { refreshVoiceDiagnostics() }
        }, sectionParams(topDp = 8))
        addView(actionButton("Começar a ouvir e falar", primary = true).apply {
            setOnClickListener { startListening() }
        }, sectionParams(topDp = 12))
        addView(actionButton("Parar voz e microfone", primary = false).apply {
            setOnClickListener { stopListening() }
        }, sectionParams(topDp = 10))
    }

    private fun buildPrivacyCard(): LinearLayout = card().apply {
        addView(sectionLabel("PRIVACIDADE"))
        remoteCognitionCheck = CheckBox(this@MainActivity).apply {
            text = "Permitir cognição remota para minhas falas"
            textSize = 15F
            setTextColor(getColor(R.color.amora_white))
            isChecked = client.isRemoteCognitionAllowed()
            setPadding(0, dp(8), 0, dp(4))
            setOnCheckedChangeListener { _, checked ->
                client.setRemoteCognitionAllowed(checked)
                setStatus(
                    if (checked) {
                        "Cognição remota permitida. O Core ainda filtra o contexto antes de qualquer provider."
                    } else {
                        "Cognição remota desligada. O Core usa a rota local/fallback."
                    }
                )
            }
        }
        addView(remoteCognitionCheck)
        addView(bodyText("Isso controla a etapa cognitiva depois da transcrição. Permissão de STT/áudio continua separada."))
    }

    private fun pair() {
        val code = codeInput.text.toString().trim()
        if (!code.matches(Regex("\\d{6}"))) {
            setStatus("Digite os 6 dígitos mostrados por `amora pair` no Termux.")
            return
        }
        setStatus("Pareando com o Core…")
        Thread {
            runCatching { client.pair(code) }
                .onSuccess {
                    runOnUiThread {
                        codeInput.text.clear()
                        pairingExpanded = false
                        refreshPairingUi()
                        setStatus("Pareada com o Core • credencial segura salva neste app. ✦")
                    }
                }
                .onFailure { error -> runOnUiThread { setStatus("Falha no pareamento: ${error.message}") } }
        }.start()
    }

    private fun testCore() {
        setStatus("Consultando o Core local…")
        Thread {
            runCatching { client.health() }
                .onSuccess { health -> runOnUiThread { setStatus("Core online • $health ✦") } }
                .onFailure { error -> runOnUiThread { setStatus("Core indisponível: ${error.message}") } }
        }.start()
    }

    private fun refreshVoiceDiagnostics() {
        voiceDiagnosticView.text = voiceDiagnostics.describe()
    }

    private fun startListening() {
        if (!client.isPaired()) {
            pairingExpanded = true
            refreshPairingUi()
            setStatus("Falta parear este app com o Core primeiro.")
            return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            val permissions = mutableListOf(Manifest.permission.RECORD_AUDIO)
            if (android.os.Build.VERSION.SDK_INT >= 33) permissions += Manifest.permission.POST_NOTIFICATIONS
            requestPermissions(permissions.toTypedArray(), REQUEST_AUDIO)
            return
        }
        startForegroundService(Intent(this, AudioCaptureService::class.java))
        val mode = if (client.isRemoteCognitionAllowed()) "remota permitida" else "local/fallback"
        setStatus("Ouvindo • VAD local • cognição $mode ✦")
        refreshVoiceDiagnostics()
    }

    private fun stopListening() {
        val intent = Intent(this, AudioCaptureService::class.java).setAction(AudioCaptureService.ACTION_STOP)
        startService(intent)
        setStatus("Voz e microfone parados.")
        refreshVoiceDiagnostics()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQUEST_AUDIO && grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            startListening()
        } else if (requestCode == REQUEST_AUDIO) {
            setStatus("Permissão de microfone negada.")
        }
    }

    private fun refreshPairingUi() {
        val paired = client.isPaired()
        pairingContainer.visibility = if (!paired || pairingExpanded) View.VISIBLE else View.GONE
        pairingToggleButton.visibility = if (paired) View.VISIBLE else View.GONE
        pairingToggleButton.text = if (pairingExpanded) "Ocultar pareamento" else "Trocar pareamento"
    }

    private fun refreshStatus() {
        val pairing = if (client.isPaired()) {
            "Pareada com o Core • credencial salva no Android Keystore."
        } else {
            "Ainda não pareada com o Core."
        }
        val cognition = if (client.isRemoteCognitionAllowed()) {
            "Cognição remota: permitida."
        } else {
            "Cognição remota: bloqueada por padrão."
        }
        setStatus("$pairing\n$cognition")
    }

    private fun setStatus(value: String) {
        statusView.text = value
    }

    private fun card(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(18), dp(18), dp(18), dp(18))
        setBackgroundResource(R.drawable.bg_amora_card)
    }

    private fun sectionLabel(value: String): TextView = TextView(this).apply {
        text = value
        textSize = 12F
        letterSpacing = 0.12F
        setTypeface(typeface, Typeface.BOLD)
        setTextColor(getColor(R.color.amora_neon))
    }

    private fun bodyText(value: String): TextView = TextView(this).apply {
        text = value
        textSize = 13F
        setTextColor(getColor(R.color.amora_text_muted))
        setPadding(0, dp(6), 0, 0)
        setLineSpacing(0F, 1.12F)
    }

    private fun actionButton(label: String, primary: Boolean): Button = Button(this).apply {
        text = label
        textSize = 15F
        isAllCaps = false
        setTypeface(typeface, Typeface.BOLD)
        setTextColor(getColor(R.color.amora_white))
        setBackgroundResource(if (primary) R.drawable.bg_amora_button_primary else R.drawable.bg_amora_button_secondary)
        backgroundTintList = null
        stateListAnimator = null
        minHeight = dp(52)
        setPadding(dp(16), dp(10), dp(16), dp(10))
    }

    private fun sectionParams(topDp: Int = 14): LinearLayout.LayoutParams =
        LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            topMargin = dp(topDp)
        }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    companion object {
        private const val REQUEST_AUDIO = 41
    }
}

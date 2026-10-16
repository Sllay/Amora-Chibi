package dev.amora.chibi.voice.lab

import android.app.Activity
import android.graphics.Typeface
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import dev.amora.chibi.R
import java.util.Locale
import java.util.concurrent.Executors

/** Standalone experimental UI used only to audition candidate Amora voices. */
class VoiceLabActivity : Activity() {
    private val executor = Executors.newSingleThreadExecutor()
    private lateinit var engine: SupertonicVoiceLabEngine
    private lateinit var statusView: TextView
    private lateinit var textInput: EditText
    private lateinit var speakerButton: Button
    private lateinit var presetButton: Button
    private lateinit var synthesizeButton: Button
    private lateinit var stopButton: Button

    @Volatile private var closing = false
    private var speakerCount = 0
    private var selectedSpeaker = 0
    private var selectedPreset = SupertonicVoiceLabEngine.Preset.NATURAL

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        engine = SupertonicVoiceLabEngine(this)
        setContentView(buildUi())
        prepareEngine()
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

        root.addView(TextView(this).apply {
            text = "AMORA • VOICE LAB"
            textSize = 26F
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(getColor(R.color.amora_white))
        })
        root.addView(TextView(this).apply {
            text = "Supertonic 3 INT8 • laboratório offline • não altera a voz de produção"
            textSize = 13F
            setTextColor(getColor(R.color.amora_pink))
            setPadding(0, dp(4), 0, dp(14))
        })

        root.addView(card().apply {
            addView(sectionLabel("STATUS"))
            statusView = bodyText("Preparando Supertonic 3 no aparelho…").also {
                it.setTextColor(getColor(R.color.amora_white))
                addView(it)
            }
        }, sectionParams())

        root.addView(card().apply {
            addView(sectionLabel("VOZ / PERFIL"))
            addView(bodyText("Troque o speaker e o perfil, gere exatamente as mesmas frases e escolha pelo ouvido. O idioma do motor fica fixo em português."))

            speakerButton = actionButton("Speaker: preparando…", primary = false).apply {
                isEnabled = false
                setOnClickListener {
                    if (speakerCount > 0) {
                        selectedSpeaker = (selectedSpeaker + 1) % speakerCount
                        refreshSelectors()
                    }
                }
            }
            addView(speakerButton, sectionParams(topDp = 12))

            presetButton = actionButton("Perfil: ${selectedPreset.label}", primary = false).apply {
                setOnClickListener {
                    val values = SupertonicVoiceLabEngine.Preset.entries
                    selectedPreset = values[(selectedPreset.ordinal + 1) % values.size]
                    refreshSelectors()
                }
            }
            addView(presetButton, sectionParams(topDp = 10))

            addView(bodyText("NATURAL = referência limpa • AMORA_SOFT = um pouco mais calma • AMORA_SYNTH = fantasia mais seca • CHIBI = fantasia mais rápida. Pitch não é alterado nesta prova."))
        }, sectionParams())

        root.addView(card().apply {
            addView(sectionLabel("FRASES FIXAS"))
            FIXED_PHRASES.forEachIndexed { index, phrase ->
                addView(actionButton("${index + 1}. $phrase", primary = false).apply {
                    gravity = Gravity.START or Gravity.CENTER_VERTICAL
                    setOnClickListener { textInput.setText(phrase) }
                }, sectionParams(topDp = if (index == 0) 10 else 7))
            }
        }, sectionParams())

        root.addView(card().apply {
            addView(sectionLabel("TEXTO LIVRE / PLAYBACK"))
            textInput = EditText(this@VoiceLabActivity).apply {
                setText(FIXED_PHRASES.first())
                hint = "Digite qualquer frase em português"
                minLines = 3
                maxLines = 7
                textSize = 16F
                setTextColor(getColor(R.color.amora_white))
                setHintTextColor(getColor(R.color.amora_text_muted))
                setBackgroundResource(R.drawable.bg_amora_input)
                setPadding(dp(14), dp(12), dp(14), dp(12))
            }
            addView(textInput, sectionParams(topDp = 10))

            synthesizeButton = actionButton("Gerar e tocar", primary = true).apply {
                isEnabled = false
                setOnClickListener { synthesize() }
            }
            addView(synthesizeButton, sectionParams(topDp = 12))

            stopButton = actionButton("Parar áudio", primary = false).apply {
                setOnClickListener {
                    engine.stop()
                    setStatus("Playback interrompido.")
                }
            }
            addView(stopButton, sectionParams(topDp = 10))
        }, sectionParams())

        root.addView(TextView(this).apply {
            text = "Voice Lab experimental • a vencedora só vira voz-base após teste físico no Galaxy A15"
            textSize = 12F
            gravity = Gravity.CENTER
            setTextColor(getColor(R.color.amora_text_muted))
            setPadding(0, dp(22), 0, dp(6))
        })

        return scroll
    }

    private fun prepareEngine() {
        setBusy(true)
        executor.execute {
            runCatching { engine.prepare() }
                .onSuccess { count ->
                    if (!closing) runOnUiThread {
                        speakerCount = count
                        selectedSpeaker = 0
                        refreshSelectors()
                        setBusy(false)
                        setStatus("Supertonic 3 pronto • $count speakers • offline • pt")
                    }
                }
                .onFailure { error ->
                    if (!closing) runOnUiThread {
                        setBusy(false, failed = true)
                        setStatus("Falha ao abrir o Voice Lab: ${describe(error)}")
                    }
                }
        }
    }

    private fun synthesize() {
        val text = textInput.text.toString().trim()
        if (text.isEmpty()) {
            setStatus("Digite ou escolha uma frase primeiro.")
            return
        }
        if (speakerCount <= 0) {
            setStatus("O motor ainda não está pronto.")
            return
        }

        val speaker = selectedSpeaker
        val preset = selectedPreset
        setBusy(true)
        executor.execute {
            runCatching {
                engine.synthesizeAndPlay(text, speaker, preset) { stage ->
                    if (!closing) runOnUiThread { setStatus(stage) }
                }
            }.onSuccess { result ->
                if (!closing) runOnUiThread {
                    setBusy(false)
                    setStatus(
                        "Concluído • speaker ${result.speakerId} • ${result.preset.label} • " +
                            "${format(result.audioSeconds)} s • RTF ${format(result.rtf)}",
                    )
                }
            }.onFailure { error ->
                if (!closing) runOnUiThread {
                    setBusy(false)
                    setStatus("Falha na síntese: ${describe(error)}")
                }
            }
        }
    }

    private fun refreshSelectors() {
        speakerButton.text = if (speakerCount > 0) {
            "Speaker: $selectedSpeaker / ${speakerCount - 1}  • toque para trocar"
        } else {
            "Speaker: indisponível"
        }
        speakerButton.isEnabled = speakerCount > 1
        presetButton.text = "Perfil: ${selectedPreset.label}  • toque para trocar"
    }

    private fun setBusy(busy: Boolean, failed: Boolean = false) {
        synthesizeButton.isEnabled = !busy && !failed && speakerCount > 0
        presetButton.isEnabled = !busy
        speakerButton.isEnabled = !busy && speakerCount > 1
        textInput.isEnabled = !busy
        stopButton.isEnabled = true
    }

    private fun setStatus(value: String) {
        statusView.text = value
    }

    private fun describe(error: Throwable): String {
        val message = error.message?.takeIf { it.isNotBlank() }
        return if (message == null) error.javaClass.simpleName else "${error.javaClass.simpleName}: $message"
    }

    private fun format(value: Double): String = String.format(Locale.US, "%.2f", value)

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
        textSize = 14F
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

    override fun onDestroy() {
        closing = true
        engine.stop()
        executor.execute { engine.close() }
        executor.shutdown()
        super.onDestroy()
    }

    companion object {
        val FIXED_PHRASES = listOf(
            "Bom dia. Como você dormiu?",
            "Eu realmente não concordo com isso.",
            "Você deixou suas coisas espalhadas pela sala.",
            "Três pratos de trigo para três tigres tristes.",
            "Essas escolhas precisam ser consideradas com cuidado.",
            "Amor, você sabe que isso não faz o menor sentido, né?",
        )
    }
}

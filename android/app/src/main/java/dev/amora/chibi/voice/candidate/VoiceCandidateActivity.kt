package dev.amora.chibi.voice.candidate

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
import dev.amora.chibi.voice.AmoraSupertonicVoiceEngine
import dev.amora.chibi.voice.AmoraVoiceExpressionPlanner
import java.util.concurrent.atomic.AtomicBoolean

/** In-APK physical gate for the exact conversation voice engine. */
class VoiceCandidateActivity : Activity() {
    private lateinit var engine: AmoraSupertonicVoiceEngine
    private lateinit var status: TextView
    private lateinit var text: EditText
    private val stopRequested = AtomicBoolean(false)
    @Volatile private var speaking = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        engine = AmoraSupertonicVoiceEngine(this)
        setContentView(buildUi())
        Thread {
            val ok = engine.prepare()
            runOnUiThread {
                status.text = if (ok) {
                    "Engine pronta. Compare NEUTRA → CARINHOSA → TRISTE → BRAVA → FELIZ."
                } else {
                    "Falha ao preparar a candidata: ${engine.lastFailure()}"
                }
            }
        }.start()
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
            text = "Amora Voice Candidate"
            textSize = 26f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(getColor(R.color.amora_white))
        })
        root.addView(TextView(this).apply {
            text = "FINAL_B + VoiceSignature V1 Soft + Emotion/Prosody V2\nGate físico: Galaxy A15 • ainda não é promoção canônica"
            textSize = 13f
            setTextColor(getColor(R.color.amora_text_muted))
            setPadding(0, dp(6), 0, dp(16))
        })

        text = EditText(this).apply {
            setText("Amor, você sabe que isso não faz o menor sentido, né?")
            hint = "Texto para testar"
            textSize = 16f
            setTextColor(getColor(R.color.amora_white))
            setHintTextColor(getColor(R.color.amora_text_muted))
            setBackgroundResource(R.drawable.bg_amora_input)
            setPadding(dp(14), dp(12), dp(14), dp(12))
            minLines = 2
        }
        root.addView(text, ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)

        status = TextView(this).apply {
            text = "Preparando engine…"
            textSize = 13f
            setTextColor(getColor(R.color.amora_neon))
            setPadding(0, dp(14), 0, dp(8))
        }
        root.addView(status)

        listOf("NEUTRA", "CARINHOSA", "TRISTE", "BRAVA", "FELIZ").forEach { label ->
            root.addView(button("Falar • $label").apply {
                setOnClickListener { speak(label) }
            }, params(8))
        }
        root.addView(button("Parar").apply {
            setOnClickListener {
                stopRequested.set(true)
                engine.stop()
                status.text = "Parada."
            }
        }, params(14))

        root.addView(TextView(this).apply {
            text = "O teste usa a mesma engine de VoiceOutput da conversa normal. Não usa o DSP antigo de playback-rate."
            textSize = 12f
            setTextColor(getColor(R.color.amora_text_muted))
            gravity = Gravity.CENTER
            setPadding(0, dp(18), 0, dp(8))
        })
        return scroll
    }

    private fun speak(label: String) {
        if (speaking) {
            status.text = "Já tem uma fala em andamento; aperte Parar primeiro."
            return
        }
        val phrase = text.text.toString().trim()
        if (phrase.isEmpty()) return
        stopRequested.set(false)
        speaking = true
        status.text = "Gerando $label…"
        Thread {
            val expression = AmoraVoiceExpressionPlanner.preset(label)
            val result = engine.play(
                text = phrase,
                expression = expression,
                volume = 1f,
                stopRequested = stopRequested,
                onStarted = { runOnUiThread { status.text = "Falando $label…" } },
            )
            val metrics = engine.lastMetrics()
            runOnUiThread {
                speaking = false
                status.text = buildString {
                    append("$label • $result")
                    if (metrics != null) {
                        append("\nTTS RTF %.3f • WORLD/signature RTF %.3f • total %.3f".format(
                            metrics.synthesisRtf,
                            metrics.nativeRtf,
                            metrics.totalRtf,
                        ))
                    }
                    engine.lastFailure()?.let { append("\nFalha: $it") }
                }
            }
        }.start()
    }

    private fun button(label: String): Button = Button(this).apply {
        text = label
        isAllCaps = false
        textSize = 15f
        setTypeface(typeface, Typeface.BOLD)
        setTextColor(getColor(R.color.amora_white))
        setBackgroundResource(R.drawable.bg_amora_button_primary)
        backgroundTintList = null
        stateListAnimator = null
        minHeight = dp(50)
    }

    private fun params(top: Int) = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT,
        ViewGroup.LayoutParams.WRAP_CONTENT,
    ).apply { topMargin = dp(top) }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    override fun onDestroy() {
        stopRequested.set(true)
        engine.close()
        super.onDestroy()
    }
}

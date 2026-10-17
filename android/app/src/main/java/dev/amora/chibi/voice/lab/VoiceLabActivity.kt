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
import dev.amora.chibi.core.CoreClient
import dev.amora.chibi.voice.LabEmotionPreset
import dev.amora.chibi.voice.VoiceProfileCatalog
import dev.amora.chibi.voice.VoiceProfileSnapshot
import dev.amora.chibi.voice.VoiceProfileStore
import java.util.Locale
import java.util.concurrent.Executors

/** Standalone experimental UI used only to audition candidate Amora voices. */
class VoiceLabActivity : Activity() {
    private val executor = Executors.newSingleThreadExecutor()
    private lateinit var engine: SupertonicVoiceLabEngine
    private lateinit var coreClient: CoreClient
    private lateinit var profileStore: VoiceProfileStore
    private lateinit var catalog: VoiceProfileCatalog
    private lateinit var statusView: TextView
    private lateinit var profileSourceView: TextView
    private lateinit var textInput: EditText
    private lateinit var speakerButton: Button
    private lateinit var profileButton: Button
    private lateinit var emotionButton: Button
    private lateinit var syncButton: Button
    private lateinit var synthesizeButton: Button
    private lateinit var stopButton: Button

    @Volatile private var closing = false
    private var speakerCount = 0
    private var selectedSpeaker = 0
    private var selectedProfileId = "NATURAL"
    private var selectedEmotion = LabEmotionPreset.NEUTRAL
    private var profileSource = "APK"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        engine = SupertonicVoiceLabEngine(this)
        coreClient = CoreClient(this)
        profileStore = VoiceProfileStore(this)
        applySnapshot(profileStore.load(SupertonicVoiceLabEngine.MODEL_ID))
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
            text = "Supertonic 3 INT8 • laboratório offline • perfis/DSP sincronizados pelo Core"
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
            profileSourceView = bodyText(profileSourceText()).also { addView(it) }

            syncButton = actionButton("Atualizar perfis do Core", primary = false).apply {
                setOnClickListener { syncProfiles() }
            }
            addView(syncButton, sectionParams(topDp = 10))
        }, sectionParams())

        root.addView(card().apply {
            addView(sectionLabel("VOZ BASE / PERFIL / EMOÇÃO"))
            addView(bodyText("Speaker = voz base. Perfil = assinatura/fantasia + DSP. Emoção = modulação pequena vinda do Core. O modelo continua local no APK; parâmetros leves de pitch/textura podem mudar pelo servidor."))

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

            profileButton = actionButton("Perfil: preparando…", primary = false).apply {
                setOnClickListener {
                    val profiles = catalog.profiles
                    val current = profiles.indexOfFirst { it.id == selectedProfileId }.coerceAtLeast(0)
                    selectedProfileId = profiles[(current + 1) % profiles.size].id
                    refreshSelectors()
                }
            }
            addView(profileButton, sectionParams(topDp = 10))

            emotionButton = actionButton("Emoção: ${selectedEmotion.label}", primary = false).apply {
                setOnClickListener {
                    val values = LabEmotionPreset.entries
                    selectedEmotion = values[(selectedEmotion.ordinal + 1) % values.size]
                    refreshSelectors()
                }
            }
            addView(emotionButton, sectionParams(topDp = 10))

            addView(bodyText("O Lab agora aplica pitch por playback com compensação de velocidade e uma cadeia digital bounded (drive, bit-depth, sample-hold e ring modulation). Formant/pitch-tracking avançados continuam para uma etapa posterior."))
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
            text = "Depois desta build, pitch/textura/drive do profile vêm de config/voice_profiles.json: GitSync + reiniciar Core + Atualizar perfis. Sem recompilar APK para cada ajuste."
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
            val engineResult = runCatching { engine.prepare() }
            if (engineResult.isFailure) {
                val error = engineResult.exceptionOrNull() ?: IllegalStateException("falha desconhecida")
                if (!closing) runOnUiThread {
                    setBusy(false, failed = true)
                    setStatus("Falha ao abrir o Voice Lab: ${describe(error)}")
                }
                return@execute
            }

            val count = engineResult.getOrThrow()
            val syncResult = if (coreClient.isPaired()) {
                runCatching { profileStore.refresh(coreClient, SupertonicVoiceLabEngine.MODEL_ID) }
            } else {
                Result.success(profileStore.load(SupertonicVoiceLabEngine.MODEL_ID))
            }

            if (!closing) runOnUiThread {
                speakerCount = count
                syncResult.onSuccess { applySnapshot(it) }
                selectedSpeaker = selectedSpeaker.coerceIn(0, (speakerCount - 1).coerceAtLeast(0))
                refreshSelectors()
                refreshProfileSource()
                setBusy(false)
                val syncNote = syncResult.exceptionOrNull()?.let { " • ${describeSyncError(it)}" }.orEmpty()
                setStatus("Supertonic 3 pronto • $count speakers • offline • ${catalog.language}$syncNote")
            }
        }
    }

    private fun syncProfiles() {
        if (!coreClient.isPaired()) {
            setStatus("Pareie o app com o Core antes de sincronizar perfis.")
            return
        }
        setBusy(true)
        setStatus("Verificando perfis de voz no Core…")
        executor.execute {
            runCatching { profileStore.refresh(coreClient, SupertonicVoiceLabEngine.MODEL_ID) }
                .onSuccess { snapshot ->
                    if (!closing) runOnUiThread {
                        applySnapshot(snapshot)
                        if (speakerCount > 0) {
                            selectedSpeaker = selectedSpeaker.coerceIn(0, speakerCount - 1)
                        }
                        refreshSelectors()
                        refreshProfileSource()
                        setBusy(false)
                        setStatus("Perfis/DSP atualizados • ${catalog.revision} • fonte $profileSource")
                    }
                }
                .onFailure { error ->
                    if (!closing) runOnUiThread {
                        setBusy(false)
                        setStatus("Falha ao atualizar perfis; mantendo cache atual: ${describeSyncError(error)}")
                    }
                }
        }
    }

    private fun applySnapshot(snapshot: VoiceProfileSnapshot) {
        catalog = snapshot.catalog
        profileSource = snapshot.source
        selectedProfileId = catalog.defaultProfileId
        selectedSpeaker = catalog.defaultSpeakerId
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
        val profile = catalog.profile(selectedProfileId) ?: catalog.defaultProfile()
        val emotion = selectedEmotion
        val modulation = catalog.emotionModulation
        val language = catalog.language
        setBusy(true)
        executor.execute {
            runCatching {
                engine.synthesizeAndPlay(
                    text = text,
                    speakerId = speaker,
                    profile = profile,
                    emotion = emotion.emotion,
                    modulation = modulation,
                    language = language,
                ) { stage ->
                    if (!closing) runOnUiThread { setStatus(stage) }
                }
            }.onSuccess { result ->
                if (!closing) runOnUiThread {
                    setBusy(false)
                    setStatus(
                        "Concluído • speaker ${result.speakerId} • ${result.profileId} • ${emotion.label} • " +
                            "pitch ${formatSigned(result.pitchSemitones.toDouble())} st • digital ${format(result.digitalMix.toDouble())} • " +
                            "speed ${format(result.effectiveSpeed.toDouble())} • silence ${format(result.effectiveSilenceScale.toDouble())} • " +
                            "gain ${format(result.effectiveGain.toDouble())} • ${format(result.audioSeconds)} s • RTF ${format(result.rtf)}",
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
            "Speaker base: $selectedSpeaker / ${speakerCount - 1}  • toque para trocar"
        } else {
            "Speaker base: indisponível"
        }
        speakerButton.isEnabled = speakerCount > 1

        val profile = catalog.profile(selectedProfileId) ?: catalog.defaultProfile()
        val dsp = profile.dsp
        profileButton.text =
            "Perfil: ${profile.label} • pitch ${formatSigned(dsp.pitchSemitones.toDouble())} st • digital ${format(dsp.digitalMix.toDouble())}"
        emotionButton.text = "Emoção: ${selectedEmotion.label}  • toque para trocar"
    }

    private fun refreshProfileSource() {
        if (::profileSourceView.isInitialized) profileSourceView.text = profileSourceText()
    }

    private fun profileSourceText(): String =
        "Config: $profileSource • revisão ${catalog.revision} • ${if (catalog.canonical) "CANÔNICA" else "LAB / não canônica"}"

    private fun setBusy(busy: Boolean, failed: Boolean = false) {
        if (!::synthesizeButton.isInitialized) return
        synthesizeButton.isEnabled = !busy && !failed && speakerCount > 0
        profileButton.isEnabled = !busy
        emotionButton.isEnabled = !busy
        speakerButton.isEnabled = !busy && speakerCount > 1
        syncButton.isEnabled = !busy
        textInput.isEnabled = !busy
        stopButton.isEnabled = true
    }

    private fun setStatus(value: String) {
        statusView.text = value
    }

    private fun describeSyncError(error: Throwable): String {
        val detail = describe(error)
        return if (detail.contains("HTTP 404")) {
            "Core desatualizado: a rota /v1/voice/profiles ainda não existe nesse processo. Faça GitSync/pull da main e reinicie `amora serve --port 8765`."
        } else {
            detail
        }
    }

    private fun describe(error: Throwable): String {
        val message = error.message?.takeIf { it.isNotBlank() }
        return if (message == null) error.javaClass.simpleName else "${error.javaClass.simpleName}: $message"
    }

    private fun format(value: Double): String = String.format(Locale.US, "%.2f", value)

    private fun formatSigned(value: Double): String = String.format(Locale.US, "%+.1f", value)

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

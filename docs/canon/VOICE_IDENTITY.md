# AMORA — Identidade Vocal Canônica

> Atualização Web (2026-10-30, ADR-0021): `WEB_SAFARI` passa a usar o clone
> aprovado da Amora no Fish Audio `s2.1-pro-free` como caminho normal de voz.
> Gemini é fallback explícito de disponibilidade. Supertonic/FINAL_B/Signature
> permanecem congelados e disponíveis por opt-in local/worker, mas não são mais
> o backend Web default.
>
> Atualização de infraestrutura (2026-10-23, ADR-0015): Core/Web foram
> preparados para host Linux remoto e VoiceBackend substituível. Esta fronteira
> é preservada pelo caminho Fish: Safari nunca recebe a API key nem chama o
> provider diretamente.

**Status:** CANÔNICO / REFERÊNCIA FINAL APROVADA / CORE→SAFARI CERTIFICATION PENDING  
**Atualizado em:** 2026-10-30  
**Backend Web primário:** Fish Audio `s2.1-pro-free` + clone persistente da Amora  
**Fallback Web:** Gemini TTS configurado  
**Backend local preservado:** sherpa-onnx + Supertonic 3 INT8  
**Base local congelada:** `AMORA_CHILD_FINAL_B_PLUS60`  
**Assinatura local congelada:** `AMORA_VOICE_SIGNATURE_V1_SOFT`

---

## 1. Princípio

> A voz é parte da identidade contínua da Amora, mas o backend de TTS é capacidade substituível.

A identidade percebida deve permanecer estável. O backend normal pode mudar
quando evidência física mostrar que outro caminho preserva melhor a referência
aprovada, desde que a troca seja registrada e observável.

Locale canônico: **pt-BR**.

Na Web atual:

```text
SpeechUtterance do Core
↓
privacy / remote-processing gate
↓
Fish S2.1 Pro Free + clone da Amora
↓
WAV
↓
Safari Web Audio
```

Se Fish falhar, Gemini pode ser usado como fallback explícito de disponibilidade
por decisão atual de BK. Essa troca não redefine a identidade canônica e deve
aparecer em diagnóstico/headers. Se ambos falharem, texto continua utilizável.

`window.speechSynthesis` não é transporte principal da voz Web.

---

## 2. Referência vocal final

A referência aprovada por BK é:

```text
Amora_Voice_Reference.wav
```

Propriedades registradas no laboratório:

- PCM s16le;
- 16 kHz;
- mono;
- duração aproximada 13.655 s;
- SHA-256 `6752794014bdd0eab6bf4d71ae0d8c97dee798231c970b691ada9552a45602be`.

Essa referência representa a identidade-alvo: feminina jovem-adulta, pt-BR,
natural, afinada e levemente virtual/sintética sem soar robótica. Não
reprocessar/destruir o original para corrigir limitações de um provider.

O clone Fish criado a partir dessa referência foi aprovado auditivamente por BK
no site do provider. A certificação do caminho **Core → Fish → Safari** ainda é
separada.

---

## 3. Perfil vocal alvo

A voz normal da Amora deve permanecer:

- feminina;
- brasileira / pt-BR natural e inteligível;
- jovem adulta;
- doce sem infantilização exagerada;
- reconhecível entre sessões;
- conversacional, não locutora/assistente;
- levemente virtual/anime sem sacrificar naturalidade;
- capaz de carinho, brincadeira, firmeza, baixa energia e irritação sem trocar de identidade-base.

Não é objetivo imitar pessoa pública, pessoa real sem autorização ou voz cuja
licença impeça o uso pretendido.

---

## 4. Caminho Web/Safari atual — ADR-0021

O navegador não chama Fish diretamente. A key fica no Core/hosting.

```text
WEB_SAFARI
→ Core /v1/web/voice/utterances/{id}/audio
→ Fish `s2.1-pro-free`
→ reference_id do clone da Amora
→ WAV
→ Safari decodeAudioData / Web Audio
```

Fallback:

```text
Fish indisponível / quota / erro
→ Gemini TTS, se configurado
→ WAV empacotado pelo Core
→ Safari
```

Falha total:

```text
Fish falha
+ Gemini falha/ausente
→ VOICE_CLOUD_TTS_UNAVAILABLE
→ texto permanece
→ fila pode cancelar/liberar a utterance
```

Configuração:

```text
AMORA_SECRET_FISH_API_KEY=<secret>
AMORA_FISH_REFERENCE_ID=<clone id>
# ou AMORA_SECRET_FISH_REFERENCE_ID=<clone id>
```

`AMORA_WEB_VOICE_BACKEND=cloud` é o default. `local` e `remote` existem apenas
como opt-in explícito para o caminho Supertonic preservado.

O modelo Fish é fixado em código como `s2.1-pro-free`; não há override para um
modelo pago.

---

## 5. Privacidade da voz remota

Fish/Gemini só recebem uma utterance quando:

- `privacy_scope` é `PUBLIC` ou `PERSONAL`; e
- `remote_tts_allowed=true` foi registrado pelo Core.

Nunca enviar `PRIVATE`, `AUTH` ou `SECRET` ao TTS cloud.

API key e `reference_id` não entram em memória cognitiva, prompt, TTS text,
headers de resposta ou `/v1/web/state`.

---

## 6. Supertonic local preservado

O caminho local não foi apagado. Backend:

```text
sherpa-onnx-supertonic-3-tts-int8-2026-05-11
```

Style base congelado:

```text
60% speaker 0
40% speaker 4
```

O style misto é materializado em `amora_voice.bin` pelo script:

```text
android/tools/build_amora_voice_bin.py
```

Ele continua sendo laboratório/fallback explícito/offline e pode continuar
servindo Android quando selecionado. Não voltar a torná-lo Web default sem nova
evidência/decisão.

---

## 7. FINAL_B — base local congelada

ADR-0010 congelou:

```text
AMORA_CHILD_FINAL_B_PLUS60
```

Parâmetros:

- target F0 median: **284.8 Hz**;
- max F0 ratio: **1.186x**;
- pitch-range base: **1.048x**;
- spectral warp:
  - 0 Hz → 1.000x;
  - 250 Hz → 1.000x;
  - 700 Hz → 1.126x;
  - 1500 Hz → 1.111x;
  - 3000 Hz → 1.068x;
  - 5000 Hz → 1.020x;
  - 8000 Hz → 1.000x;
- high-frequency shelf: **-2.36 dB**.

Não reabrir F0, VTL/warp ou o mix 60/40 apenas porque outra camada/provider
ficou ruim.

---

## 8. VoiceSignature V1 Soft — assinatura local congelada

ADR-0011 aceitou:

```text
AMORA_VOICE_SIGNATURE_V1_SOFT
```

A assinatura preserva FINAL_B e adiciona apenas:

- WORLD aperiodicity scale: **0.965x**;
- harmonic drive: **1.30**;
- harmonic wet: **0.05**;
- RMS matching para preservar energia percebida.

Continuam fora da assinatura normal:

- pitch shift extra;
- novo formant/VTL;
- bitcrush;
- sample-hold;
- ring modulation.

No Fish, não aplicar automaticamente esse DSP sobre o clone aprovado: a
referência já contém a identidade desejada. O Core deve primeiro preservar a
saída do clone e só introduzir pós-processamento se houver evidência física.

---

## 9. Emotion / Prosody

A semântica emocional continua pertencendo ao Core. Nem Fish nem Gemini ganham
autoridade sobre estado emocional, relação ou intenção.

O pipeline local Supertonic ainda suporta `AMORA_VOICE_EXPRESSION_V2` bounded.
Para Fish, controles/tags expressivos são capability do renderer e devem ser
mapeados separadamente; não inventar tags no texto sem contrato/teste.

Invariante:

```text
identidade vocal = fixa
expressão emocional = variável e bounded
```

---

## 10. Verdade de entrega

Renderizar áudio não significa que a fala foi ouvida.

Contrato permanece:

```text
QUEUED
→ CLAIMED
→ render áudio
→ player começa
→ started
→ player termina
→ ACK spoken=true
```

Interrupção:

```text
barge-in / Parar fala / erro de voz
→ abortar fetch/player
→ cancelar utterance/turno no Core
→ liberar fila/interface
```

O texto visível da resposta não depende do sucesso do áudio.

---

## 11. Observabilidade

A Web deve expor sem secrets:

- chain configurado;
- Fish configurado sim/não;
- Gemini configurado sim/não;
- provider/modelo efetivamente usado na última renderização;
- erro bounded;
- sample rate/RTF quando disponível.

Headers de áudio indicam `X-Amora-TTS-Provider` e `X-Amora-TTS-Model`.
Quando Gemini for fallback, incluir `X-Amora-TTS-Fallback: gemini`.

---

## 12. Provenance e custo

A referência vocal é artefato próprio do projeto. O clone remoto é uma
capability substituível.

O caminho Fish Web atual só pode usar:

```text
s2.1-pro-free
```

Se essa rota deixar de ser zero-cost, a arquitetura deve degradar/falhar ou
migrar provider. Não alterar automaticamente para `s2.1-pro` pago.

Gemini fallback usa somente configuração já explicitamente fornecida ao Core;
ausência de credencial não quebra o texto.

---

## 13. Gate físico

Apesar da aprovação do clone no site Fish, ainda validar no fluxo real:

- Core hospedado → Fish → Safari/iPhone;
- pt-BR/inteligibilidade;
- preservação da identidade da referência;
- primeira latência;
- tempo total de geração;
- frases curtas e longas;
- pronúncia de Amora/BK/Sllay/gírias;
- artefatos/gaguejos;
- barge-in/manual stop;
- fallback Fish → Gemini;
- falha Fish + Gemini preservando texto;
- ausência de secrets no browser/observabilidade;
- comportamento quando `remote_tts_allowed=false`.

Até esse gate, `/v1/web/state` continua reportando:

```text
canonical_voice=false
```

Isso significa que o transporte final ainda precisa de certificação, não que a
referência vocal esteja indefinida.

---

## 14. Critério para revisitar

Revisitar quando houver evidência clara de:

- Fish não preservar no Core/Safari a qualidade aprovada no site;
- mudança do acesso/custo de `s2.1-pro-free`;
- latência/reliability inadequadas;
- fallback Gemini causar troca de timbre inaceitável;
- backend local novo reproduzir a referência com qualidade e velocidade prática;
- nova decisão explícita de BK.

Problema de transporte, fila, Web Audio, STT ou emoção deve ser corrigido na
camada dona do problema, não destruindo a referência vocal aprovada.

# AMORA — Identidade Vocal Canônica


> Atualização de infraestrutura (2026-10-23, ADR-0015): Core/Web serão
> preparados para host Linux remoto e a mesma voz congelada para worker
> independente. Koyeb/Turso/Modal são candidatos substituíveis. Descrições
> Termux/local abaixo registram o caminho implementado anterior, preservado
> para desenvolvimento. Esta decisão muda localização de compute/storage,
> não identidade, semântica, consentimento, custo ou parâmetros da voz.

**Status:** CANÔNICO / IDENTIDADE DE LABORATÓRIO CONGELADA / CERTIFICAÇÃO FÍSICA PENDENTE  
**Atualizado em:** 2026-10-22  
**Backend candidato:** sherpa-onnx + Supertonic 3 INT8  
**Base aprovada de laboratório:** `AMORA_CHILD_FINAL_B_PLUS60`  
**Assinatura aprovada de laboratório:** `AMORA_VOICE_SIGNATURE_V1_SOFT`

---

## 1. Princípio

> A voz é parte da identidade contínua da Amora, mas o backend de TTS é capacidade substituível.

A identidade percebida deve permanecer estável por meio de uma composição versionada de:

```text
TtsBackend
↓
speaker/style base
↓
VoiceSignature
↓
Emotion / Prosody bounded
```

A voz normal deve priorizar execução local/offline, pt-BR, custo sustentável e ausência de troca silenciosa de identidade.

Gemini TTS não é caminho normal da voz da Amora. `window.speechSynthesis` não é transporte principal do laboratório Safari.

---

## 2. Perfil vocal alvo

A voz normal da Amora deve permanecer:

- feminina;
- brasileira / pt-BR natural e inteligível;
- jovem;
- doce sem infantilização exagerada;
- reconhecível entre sessões;
- conversacional, não locutora/assistente;
- capaz de carinho, brincadeira, firmeza, baixa energia e irritação sem trocar de identidade-base;
- expressiva por pequenas modulações de prosódia, não por troca de timbre.

Não é objetivo imitar pessoa pública, pessoa real sem autorização ou voz cuja licença impeça o uso pretendido.

---

## 3. Backend e style aprovados para o laboratório atual

Backend:

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

Não voltar a escolher speaker/style base por sessão ou por emoção.

---

## 4. FINAL_B — base vocal congelada

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

A busca por idade/tamanho vocal base fica encerrada por padrão.

Não reabrir F0, VTL/warp ou o mix 60/40 apenas porque outra camada ficou ruim.

---

## 5. VoiceSignature V1 Soft — assinatura congelada

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

Invariante:

```text
identidade vocal = fixa
expressão emocional = variável e bounded
```

---

## 6. Emotion / Prosody V2

O Core produz `AMORA_VOICE_EXPRESSION_V2` com parâmetros bounded.

Faixas atuais do contrato:

- rate: 0.90–1.08;
- pause: 0.88–1.16;
- energy: 0.88–1.06;
- pitch range: 0.90–1.08;
- F0 register: 0.97–1.02;
- softness: 0.0–1.0;
- firmness: 0.0–1.0.

Emoção não pode:

- trocar style/speaker;
- reabrir FINAL_B;
- reescrever a VoiceSignature;
- transformar raiva em pitch/volume bruto sem controle;
- degradar inteligibilidade como efeito estilístico;
- escolher outro TTS silenciosamente se o worker falhar.

Falha de expressão cai para neutro; falha de voz não altera a identidade para outro backend.

---

## 7. Implementação compartilhada Android ↔ Termux

A implementação de FINAL_B + VoiceSignature + modulação bounded vive em:

```text
android/app/src/main/cpp/amora_voice_processor.cpp
android/app/src/main/cpp/amora_voice_processor.h
```

Esse código é compartilhado de duas formas:

```text
Android
  JNI wrapper
    ↓
amora_voice_processor.cpp

Termux / Web Voice Worker
  ctypes / C ABI
    ↓
libamora_voice_processor.so
    ↓
amora_voice_processor.cpp
```

Não manter uma segunda implementação Python “parecida” do DSP aprovado.

O objetivo é que os dois transports usem a mesma matemática de identidade, mesmo que o player final seja diferente.

---

## 8. Caminho Web/Safari atual

ADR-0013 tornou Safari a superfície primária de iteração rápida. ADR-0014 substituiu o TTS nativo do Safari pela própria voz local da Amora.

Pipeline:

```text
Core
→ SpeechUtterance validada
→ AmoraLocalVoiceRenderer no Termux
→ sherpa-onnx Supertonic 3
→ amora_voice.bin 60/40
→ shared WORLD FINAL_B
→ VoiceSignature V1 Soft
→ Emotion / Prosody V2 bounded
→ WAV PCM16 mono
→ Safari AudioContext / Web Audio
```

Responsabilidade do Safari:

- cliente/UI;
- microfone/VAD;
- player;
- pareamento;
- cancelamento/barge-in local.

Responsabilidade do Core/worker:

- identidade vocal;
- style;
- prosódia bounded;
- síntese;
- DSP;
- WAV;
- estado de entrega.

`WEB_SAFARI` e `DEVICE_A15` são destinos diferentes de fila.

---

## 9. Verdade de entrega

Renderizar WAV não significa que a fala foi ouvida.

Contrato:

```text
QUEUED
→ CLAIMED
→ render WAV
→ player começa
→ started
→ player termina
→ ACK spoken=true
```

Interrupção:

```text
barge-in / Parar fala / erro de voz
→ abortar fetch
→ parar player
→ cancelar utterance/turno no Core
→ liberar fila/interface
```

O texto visível da resposta não depende do sucesso do áudio.

Falha de voz local deve ser explícita e fail-open para a interface; não pode prender todas as mensagens seguintes.

---

## 10. Setup Termux

A preparação Web local vive em:

```text
tools/web/setup-local-voice.sh
```

Ela prepara:

- `sherpa-onnx==1.13.8` no venv do Core;
- Supertonic 3 INT8;
- `amora_voice.bin` 60/40;
- WORLD pinado;
- `libamora_voice_processor.so`;
- cache reutilizável em `~/.amora/cache/web-local-voice`.

Modelos/runtime ficam sob `~/.amora`, fora do Git.

A instalação pesada deve acontecer uma vez; iterações normais não devem reinstalar modelo nem exigir APK.

---

## 11. Perfis temporários

A Amora pode possuir perfis/fantasias vocais no futuro, mas eles não substituem a voz normal silenciosamente.

Exemplos históricos/experimentais:

```text
CHIBI
SYNTH
VIRTUAL_SINGER
MASC_JOKE
```

Qualquer perfil temporário deve ser ativado explicitamente e não altera self/memória/identidade.

---

## 12. Provenance e licença

Qualquer backend/speaker candidato precisa de provenance e licença compatíveis com o uso pretendido.

Aceitável:

- modelo local com licença compatível;
- voices/styles incluídos sob termos adequados;
- material próprio com consentimento explícito;
- modelo próprio futuro se houver vantagem comprovada.

Não aceitável como base normal:

- clone de pessoa pública sem autorização;
- origem obscura;
- provider remoto que muda voz por quota;
- fallback silencioso que substitua a identidade acústica.

---

## 13. Gate físico

A assinatura atual foi aprovada por BK como identidade de laboratório, mas CI não certifica a experiência física final.

Ainda medir no hardware real:

- pt-BR/inteligibilidade no caminho final;
- pronúncia de Amora/BK/gírias;
- primeira latência;
- RTF;
- RAM/CPU;
- temperatura;
- estabilidade longa;
- barge-in real;
- playback Safari/iPhone;
- funcionamento offline do worker local;
- ausência de chamadas TTS remotas;
- consistência acústica entre Android e Termux quando comparável.

Até esse gate, a API Web deve continuar reportando:

```text
canonical_voice=false
```

Isso significa “certificação física/final pendente”, não “identidade vocal indefinida”.

---

## 14. Critério para revisitar FINAL_B / Signature

Não reabrir os parâmetros congelados sem evidência nova clara, como:

- regressão física no Galaxy A15/Safari;
- inteligibilidade inadequada em uso real;
- artefato inevitável causado pelo próprio FINAL_B/Signature;
- divergência comprovada entre runtimes;
- backend local rights-clear claramente superior.

Problema de transporte, fila, Web Audio, STT ou emoção deve ser corrigido na camada dona do problema, não alterando a identidade vocal por reflexo.

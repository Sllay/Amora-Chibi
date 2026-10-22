# PROJECT_STATE — Amora-Chibi

**Última atualização:** 2026-10-22  
**Objetivo:** permitir continuidade segura entre chats/agentes sem depender do histórico integral da conversa.

Este arquivo é um **snapshot operacional**. Para regras atuais, leia primeiro `docs/canon/*`, `docs/roadmap/VERSIONS.md` e ADRs relevantes.

---

## 1. Fonte de verdade

Ordem de leitura atual:

1. `docs/canon/SKILL.md`;
2. `docs/canon/ARCHITECTURE.md`;
3. `docs/canon/GDD.md`;
4. `docs/canon/DIALOGUE_EMOTION.md`;
5. `docs/canon/PROVIDER_MESH.md`;
6. `docs/canon/VOICE_IDENTITY.md`;
7. `docs/roadmap/VERSIONS.md`;
8. ADRs relevantes em `docs/decisions/`;
9. este `PROJECT_STATE.md`;
10. código, `main`, PRs e workflows atuais.

GitHub é a fonte canônica. Resumos de chat são contexto auxiliar.

---

## 2. Estado operacional atual

### Superfície primária de iteração

Safari/Web é agora o laboratório conversacional principal, conforme ADR-0013.

```text
Amora Core / estado canônico
        |
        +-- Web/Safari -> superfície primária de iteração rápida
        |
        +-- Android debug -> referência / gate secundário
```

O APK debug atual não define o aplicativo Android oficial final e não deve voltar a ser o caminho padrão para cada pequeno teste.

### Web/PWA já integrado

O Safari Lab possui:

- pareamento de 6 dígitos;
- autenticação reutilizando o Core;
- chat por texto;
- microfone via Web Audio;
- STT;
- VAD/browser barge-in;
- fila de respostas;
- diagnóstico;
- `test_mode=true` por padrão;
- Cloudflare Quick Tunnel para HTTPS de desenvolvimento;
- routing separado `WEB_SAFARI`.

O bug inicial em que Safari buscava `WEB_SAFARI` mas o Core enfileirava em `DEVICE_A15` foi corrigido e integrado antes da voz local.

---

## 3. Voz Web local — integração atual

PR #54 foi integrado ao `main` no commit:

```text
5ea4bd7d40e85cf7380f50f0aad05b54605f0ade
```

O head original da branch `feat/web-local-amora-voice-worker` foi:

```text
e3f85799831127adb42f643213ed6b3d9e2d8470
```

O squash do `main` e o head original possuem a mesma árvore Git:

```text
65610c5890b41fbddae5a475018d2a9c8e232dc3
```

Portanto a implementação da branch interrompida não ficou perdida; ela foi consolidada no `main`.

### Pipeline

```text
Core
→ SpeechUtterance validada
→ AmoraLocalVoiceRenderer no Termux
→ sherpa-onnx Supertonic 3 INT8
→ amora_voice.bin 60/40
→ shared WORLD FINAL_B
→ AMORA_VOICE_SIGNATURE_V1_SOFT
→ Emotion / Prosody V2 bounded
→ WAV PCM16 mono
→ Safari AudioContext / Web Audio
```

Safari é apenas cliente, microfone, player e interface.

Não usar como transporte principal:

- `window.speechSynthesis`;
- Gemini TTS;
- fallback silencioso para outra voz.

---

## 4. Identidade vocal congelada

### Supertonic

```text
sherpa-onnx-supertonic-3-tts-int8-2026-05-11
```

Style base:

```text
60% speaker 0 + 40% speaker 4
```

`amora_voice.bin` é gerado por:

```text
android/tools/build_amora_voice_bin.py
```

### FINAL_B

```text
AMORA_CHILD_FINAL_B_PLUS60
```

Parâmetros congelados:

- target F0: 284.8 Hz;
- max F0 ratio: 1.186;
- pitch range: 1.048;
- warp: 0→1.000, 250→1.000, 700→1.126, 1500→1.111, 3000→1.068, 5000→1.020, 8000→1.000;
- shelf: -2.36 dB.

### VoiceSignature

```text
AMORA_VOICE_SIGNATURE_V1_SOFT
```

- aperiodicity scale: 0.965;
- harmonic drive: 1.30;
- harmonic wet: 0.05.

BK aprovou essa identidade como assinatura de laboratório. Não reabrir sem evidência nova.

Emotion/Prosody V2 continua variável e bounded e não altera a identidade-base.

---

## 5. Implementação compartilhada

O DSP de FINAL_B + Signature + expressão bounded vive em uma única implementação C++:

```text
android/app/src/main/cpp/amora_voice_processor.cpp
android/app/src/main/cpp/amora_voice_processor.h
```

Uso:

```text
Android JNI
    ↓
shared processor C++

Termux worker / ctypes
    ↓
libamora_voice_processor.so
    ↓
shared processor C++
```

Não manter uma versão Python aproximada em paralelo.

---

## 6. Contrato Web de delivery

Texto e áudio são desacoplados para falha graciosa.

O cliente:

1. reivindica `SpeechUtterance` de `WEB_SAFARI`;
2. mostra o texto imediatamente;
3. pede WAV autenticado ao Core;
4. decodifica com Web Audio;
5. envia `started` quando o player começa;
6. envia ACK `spoken=true` somente após o player terminar;
7. em barge-in/manual stop, aborta fetch/player e cancela no Core;
8. em falha de voz, mantém o texto, cancela o turno afetado e libera novas mensagens.

A fila Web não deve travar o chat quando o worker estiver indisponível.

`WEB_SAFARI != DEVICE_A15` continua sendo invariante de routing.

---

## 7. Setup local Termux

Script:

```text
tools/web/setup-local-voice.sh
```

Ele prepara:

- `sherpa-onnx==1.13.8` no venv do Core;
- modelo Supertonic 3 INT8;
- `amora_voice.bin` 60/40;
- WORLD pinado no commit `d625e7608ca23a870018f01e7c562ac683d9847f`;
- `libamora_voice_processor.so`;
- validação final de disponibilidade do renderer.

Downloads reutilizáveis ficam em:

```text
~/.amora/cache/web-local-voice
```

Modelos/vendor/lib ficam sob `~/.amora`, fora do Git.

A intenção é instalação pesada única. Depois disso, mudanças normais Web/Core devem exigir apenas atualização do código, restart do Core e refresh do Safari.

---

## 8. Evidência automatizada

No head original `e3f85799831127adb42f643213ed6b3d9e2d8470` do PR #54:

- Core tests: PASS em Python 3.11, 3.12, 3.13 e 3.14;
- doctor smoke: PASS;
- autonomy smoke: PASS;
- actions smoke: PASS;
- conversation smoke: PASS;
- avatar smoke: PASS;
- backup smoke: PASS;
- Android build/test: PASS.

A implementação mergeada em `main` possui a mesma árvore Git desse head.

Testes Web cobrem/serão mantidos para:

- rota WAV autenticada;
- indisponibilidade do worker;
- routing `WEB_SAFARI`;
- `test_mode`;
- ausência de `speechSynthesis` no cliente V2;
- ACK após playback explícito;
- cancelamento manual/barge-in;
- recuperação após falha de voz sem prender a próxima mensagem.

---

## 9. O que AINDA NÃO é PASS

```text
CI PASS
!=
CERTIFICAÇÃO FÍSICA PASS
```

Ainda precisa de teste real de BK:

- executar o setup no Termux do Galaxy A15;
- confirmar que o worker carrega sherpa-onnx/modelo/WORLD;
- abrir Safari/iPhone pelo tunnel;
- ouvir a voz real;
- validar primeira latência e RTF;
- validar barge-in/manual stop;
- observar estabilidade prolongada;
- observar RAM/CPU/temperatura;
- confirmar que falha de voz não bloqueia texto;
- confirmar ausência de chamadas TTS remotas.

A API Web continua corretamente com:

```text
canonical_voice=false
```

Isso representa certificação física/final pendente, não identidade vocal indefinida.

---

## 10. Fluxo operacional desejado

### Primeira preparação da voz Web ou quando dependências/DSP mudarem

```bash
source ~/.venvs/amora/bin/activate
cd ~/amora-src
bash tools/web/setup-local-voice.sh
```

### Iteração normal depois do setup

```text
git pull / GitSync
→ reiniciar Core
→ atualizar Safari
→ testar
```

Sem APK novo e sem reinstalar modelos a cada pequena alteração.

---

## 11. Próximo trabalho imediato

Ordem atual:

```text
1. fechar CI da regressão/ documentação do Voice Worker Web
2. integrar o fechamento ao main
3. rodar setup-local-voice.sh uma vez no Termux real
4. testar voz física pelo Safari/iPhone
5. coletar latência/RTF/barge-in/estabilidade
6. corrigir somente bugs comprovados no gate físico, preservando FINAL_B/Signature
7. continuar iteração da Amora pelo Safari
```

Não voltar para:

- Safari `speechSynthesis` como transporte principal;
- Gemini TTS como fallback oculto;
- APK-per-small-test;
- retuning de FINAL_B/VoiceSignature sem evidência nova.

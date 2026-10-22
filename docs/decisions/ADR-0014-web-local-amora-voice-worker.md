# ADR-0014 — Local Amora Voice Worker for Safari/Web

**Status:** ACCEPTED  
**Data:** 2026-10-22  
**Owners:** BK + AMORA IA  
**Refines:** ADR-0013  
**Related:** ADR-0010, ADR-0011, ADR-0012

## Contexto

ADR-0013 moveu o laboratório principal de iteração do APK Android para Safari/Web e permitiu `window.speechSynthesis` apenas como transporte de voz de depuração enquanto o fluxo de texto, STT, fila e barge-in era validado.

Esse transporte cumpriu o objetivo inicial, mas o Safari demonstrou comportamento inadequado para continuar como caminho principal: a síntese podia não produzir áudio e os eventos `onstart`/`onend`/`onerror` não eram confiáveis o bastante para representar verdade de entrega. Manter a fila de `SpeechUtterance` dependente desses eventos criaria um patch loop e deixaria a identidade acústica fora do controle da Amora.

Em paralelo, ADR-0010 e ADR-0011 congelaram a identidade vocal aprovada de laboratório:

```text
Supertonic 3 INT8
60% speaker 0 + 40% speaker 4
        ↓
AMORA_CHILD_FINAL_B_PLUS60
        ↓
AMORA_VOICE_SIGNATURE_V1_SOFT
        ↓
Emotion / Prosody V2 bounded
```

`sherpa-onnx` 1.13.8 passou a suportar Python no Termux/Android, permitindo executar o mesmo backend Supertonic diretamente no processo local do Core sem depender de APK novo e sem usar Gemini TTS.

## Decisão

O Safari/Web deixa de usar `window.speechSynthesis` como transporte principal.

O caminho de voz do laboratório Web passa a ser:

```text
Core
→ SpeechUtterance validada
→ Voice Worker local no Termux
→ sherpa-onnx Supertonic 3 INT8
→ amora_voice.bin (60/40)
→ WORLD FINAL_B compartilhado
→ VoiceSignature V1 Soft compartilhada
→ Emotion/Prosody V2 bounded
→ WAV PCM16 mono
→ Safari Web Audio / AudioContext
```

O navegador continua sendo apenas cliente de plataforma:

- UI;
- pareamento/autenticação;
- microfone/VAD;
- envio para STT;
- player Web Audio;
- cancelamento/barge-in;
- diagnóstico.

A identidade, fila, delivery state, emoção/prosódia e renderização da voz pertencem ao Core/runtime local.

## DSP compartilhado

O pós-processamento aprovado não deve possuir uma versão Python aproximada separada.

`android/app/src/main/cpp/amora_voice_processor.cpp/.h` é a implementação compartilhada de:

- FINAL_B;
- VoiceSignature V1 Soft;
- modulação bounded da expressão V2.

Android chama esse processador pelo JNI. O worker Termux compila o mesmo código como `libamora_voice_processor.so` e o carrega por `ctypes`.

Isso preserva uma única implementação da assinatura entre os dois caminhos.

## Parâmetros congelados

Não reabrir sem evidência nova.

### `AMORA_CHILD_FINAL_B_PLUS60`

- style: 60% speaker 0 + 40% speaker 4;
- target F0: 284.8 Hz;
- max F0 ratio: 1.186;
- pitch range: 1.048;
- warp:
  - 0 Hz → 1.000;
  - 250 Hz → 1.000;
  - 700 Hz → 1.126;
  - 1500 Hz → 1.111;
  - 3000 Hz → 1.068;
  - 5000 Hz → 1.020;
  - 8000 Hz → 1.000;
- shelf: -2.36 dB.

### `AMORA_VOICE_SIGNATURE_V1_SOFT`

- aperiodicity scale: 0.965;
- harmonic drive: 1.30;
- harmonic wet: 0.05.

Emotion/Prosody V2 pode variar apenas dentro dos bounds canônicos e não reescreve essa identidade.

## Contrato de entrega Web

Texto e áudio são independentes para fins de UX:

1. o texto da Amora aparece assim que a utterance é reivindicada pelo cliente;
2. o cliente solicita o WAV autenticado ao Core;
3. falha de renderização de voz não remove o texto nem troca silenciosamente de TTS;
4. `started` só é enviado quando o `AudioBufferSourceNode` começa;
5. ACK `spoken=true` só é enviado depois de `onended` de reprodução real;
6. barge-in/manual stop aborta fetch e playback e envia cancelamento ao Core;
7. erro de voz cancela o turno afetado e libera a interface/fila para turnos seguintes;
8. `WEB_SAFARI` permanece separado de `DEVICE_A15`.

Gemini TTS não é fallback desse caminho. Safari TTS nativo não é fallback desse caminho.

## Setup local

O setup Termux deve ser pesado apenas na primeira preparação e idempotente depois:

- `sherpa-onnx==1.13.8` no venv do Core;
- modelo `sherpa-onnx-supertonic-3-tts-int8-2026-05-11`;
- geração de `amora_voice.bin` pelo script Android canônico;
- WORLD pinado em commit conhecido;
- compilação de `libamora_voice_processor.so`;
- downloads reutilizáveis sob `~/.amora/cache/web-local-voice`;
- runtime/modelos sob `~/.amora`.

## Evidência de integração

A implementação inicial foi integrada pelo PR #54 no commit:

```text
5ea4bd7d40e85cf7380f50f0aad05b54605f0ade
```

O head original da branch foi:

```text
e3f85799831127adb42f643213ed6b3d9e2d8470
```

Ambos apontam para a mesma árvore Git `65610c5890b41fbddae5a475018d2a9c8e232dc3`; o `main` contém o squash da branch.

No head do PR #54 passaram:

- Core tests: Python 3.11, 3.12, 3.13 e 3.14;
- Android build/test.

## O que esta decisão NÃO certifica

CI PASS não equivale a certificação física.

Ainda depende de teste real de BK:

- instalação/preparação do worker no Termux;
- síntese Supertonic + WORLD no Galaxy A15;
- reprodução pelo Safari/iPhone via Web Audio;
- latência/RTF percebidos;
- barge-in real;
- estabilidade longa;
- CPU/RAM/temperatura;
- qualidade acústica no caminho Web.

A identidade é aprovada como assinatura de laboratório, mas `canonical_voice=false` permanece correto até o gate físico/final aplicável.

## Consequências

Positivas:

- não há APK por pequena iteração Web;
- voz Web usa a própria assinatura da Amora;
- Android e Termux compartilham o DSP;
- falha de voz não bloqueia texto;
- nenhuma quota Gemini entra no caminho;
- o loop normal depois do setup tende a ser `git pull → reiniciar Core → atualizar Safari → testar`.

Tradeoffs:

- primeira instalação no Termux é pesada;
- síntese + WORLD consomem CPU local;
- Safari ainda exige gesto/autorização para liberar Web Audio e HTTPS para microfone;
- teste físico continua obrigatório.

## Critério para revisitar

Reabrir esta decisão apenas se houver evidência clara de que:

1. o worker local não consegue manter latência/estabilidade aceitáveis no hardware;
2. a implementação compartilhada diverge acusticamente entre Android e Termux;
3. um backend local rights-clear claramente superior substituir Supertonic;
4. o contrato Web Audio se mostrar estruturalmente inviável no Safari.

Não revisitar apenas porque `speechSynthesis` parece mais simples em um teste isolado.

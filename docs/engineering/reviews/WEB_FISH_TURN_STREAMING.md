# Web Fish turn streaming — revisão de implementação

Data: 2026-10-30  
Base: `main` / `6bb90849ff81c4a58e0a4a05f6892721dc268af6`  
Branch: `feat/web-fish-turn-streaming`  
Decisão: ADR-0022

## Problema observado fisicamente

BK confirmou que o clone Fish já toca no Safari e, na maior parte do tempo, preserva uma voz bonita. O bloqueio passou a ser transporte: respostas longas falavam poucos segundos, esperavam muito antes do próximo trecho, às vezes mostravam texto sem áudio e podiam parar vocalmente no meio do turno.

## Root cause arquitetural

O caminho Web ainda herdava a estratégia do TTS local:

1. `SpeechSegmenter` quebrava o plano em trechos curtos;
2. cada trecho virava outra `SpeechUtterance`;
3. cada utterance disparava outra requisição Fish;
4. Core recebia a resposta HTTP em streaming, mas remontava um WAV completo;
5. Safari fazia `response.arrayBuffer()` + `decodeAudioData` antes de começar playback;
6. falha de um trecho podia suprimir o restante vocal do `cognitive_turn_id` enquanto o texto permanecia.

Fish já suporta áudio incremental; o projeto já possuía `StreamingVoiceBackend` e `PcmStreamPlayer`, porém sem ligação entre as duas pontas.

## Implementação

- `WEB_SAFARI` passa a enfileirar uma única utterance de delivery por turno cognitivo;
- os `SpeechSegment` continuam persistidos para journal/rastreabilidade;
- Fish usa `format=pcm`, `latency=balanced`, `chunk_length=200`;
- Core retransmite PCM16LE autenticado por `StreamingResponse`;
- Safari usa Fetch `ReadableStream` + `PcmStreamPlayer` com ~60 ms de buffer inicial;
- backpressure do browser mantém o buffer bounded;
- `started` só é enviado quando playback PCM começa de fato;
- ACK só ocorre após o último sample agendado terminar;
- WAV completo continua como compatibilidade;
- Fish→Gemini só pode ocorrer antes do primeiro PCM Fish;
- depois que Fish começou, erro encerra a entrega em vez de trocar a voz no meio.

## Expressão

Emotion Engine permanece autoridade. A expressão Fish é tradução provider-specific de `VoiceExpressionStyle` e cues já determinados pelo Core. Tags não entram em `DisplayText`, memória ou journal literal.

Mapeamento inicial bounded é registrado na ADR-0022 e em `docs/canon/VOICE_IDENTITY.md`.

## Segurança / privacidade

- API key e `reference_id` continuam apenas no Core;
- stream exige autenticação Web;
- gates `PUBLIC|PERSONAL` + `remote_tts_allowed=true` permanecem;
- nenhum secret entra em headers/estado/browser;
- nenhum DSP FINAL_B/Signature é aplicado ao clone cloud.

## Gates automáticos esperados

- testes Fish PCM incremental e fallback pré-áudio;
- contrato de stream Core;
- delivery truth `claim → started → player end → ACK`;
- persistência dos segmentos cognitivos com única utterance Web;
- PcmStreamPlayer progressive playback/underrun/backpressure/stop;
- regressões Core/Avatar/security/container.

## Gate físico obrigatório após merge/deploy

1. enviar texto pelo teclado e confirmar que a voz começa antes de a síntese completa terminar;
2. repetir via microfone/STT;
3. usar resposta longa e confirmar ausência das pausas antigas entre segmentos;
4. confirmar `fish · stream PCM · DSP none` no diagnóstico;
5. observar underruns;
6. testar stop e barge-in;
7. observar expressão em respostas com emoção/cue reais sem exagero;
8. testar fallback Fish→Gemini antes do primeiro áudio;
9. tratar tela bloqueada/background como gate de Safari/iOS separado;
10. só depois considerar o transporte fisicamente certificado.

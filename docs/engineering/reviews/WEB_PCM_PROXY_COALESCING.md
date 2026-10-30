# Web PCM proxy coalescing hardening

Data: 2026-10-30.
Base: main `6a98dc89578fa21b29515b20d9dc265572312243` (PR #67).

## Problema

`ReadableStream` no navegador nao preserva necessariamente os mesmos limites de chunk emitidos pelo servidor/upstream. Um proxy, ingress ou o proprio browser pode coalescer varios chunks PCM em um unico `reader.read()`.

O `PcmStreamPlayer` anterior rejeitava qualquer push que fizesse `bufferedSeconds` ultrapassar `maxBufferSeconds`. Assim, um bloco HTTP grande podia gerar `PCM_BUFFER_FULL` mesmo quando a taxa media do stream estava correta e o backpressure do cliente funcionava.

## Correcao

O player agora separa duas ideias:

- backlog de transporte ja recebido, que pode exceder temporariamente a janela por coalescing inevitavel;
- horizonte efetivamente agendado no Web Audio, que continua limitado por `maxBufferSeconds`.

O player agenda fatias de ate 250 ms e reabastece a janela conforme os nodes terminam. `state().bufferedSeconds` continua incluindo backlog + audio agendado, de modo que o loop de Fetch para de ler novos bytes enquanto o backlog drena.

## Invariantes preservados

- identidade de sessao/turno/utterance e sequencia continuam validadas;
- formato/sample rate/canais nao podem mudar no meio do stream;
- `stop()` cancela nodes e backlog;
- ACK continua somente depois de playback terminado;
- nenhuma mudanca em cognicao, TTS, secrets ou privacy gates;
- WAV fallback permanece inalterado.

## Regressao

`tests/web_audio_player.test.mjs` simula um unico bloco de 1 segundo chegando quando a janela de scheduling e apenas 100 ms. O teste exige:

- nenhuma excecao `PCM_BUFFER_FULL`;
- no maximo 100 ms agendados a frente;
- backlog restante observavel;
- refill progressivo;
- termino unico depois de todo backlog tocar.

Os workflows Web/Avatar, Core e Pre-deploy security passaram no head da branch antes da abertura do PR.

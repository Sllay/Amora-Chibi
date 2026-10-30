# ADR-0022 — Streaming PCM por turno e expressão Fish derivada do Core

Status: ACCEPTED por solicitação explícita de BK.
Data: 2026-10-30.
Refina: ADR-0019 e ADR-0021.
Superseded by: none.

## Contexto

O clone Fish aprovado passou a funcionar fisicamente no Safari, mas o caminho Web ainda preservava uma estratégia desenhada para o renderer local Supertonic/WORLD: a resposta cognitiva era quebrada em segmentos curtos, cada segmento virava uma `SpeechUtterance`, cada utterance disparava outra requisição TTS e o navegador só iniciava playback depois de receber e decodificar o WAV completo.

A Fish já entrega áudio progressivamente. O SDK/API atual permite `format=pcm`, `latency=balanced`, `chunk_length` bounded e streaming de bytes. O projeto também já possuía, desde ADR-0019, `StreamingVoiceBackend`, `VoiceSession`, `VoiceChunk` e `PcmStreamPlayer`, mas essa costura ainda não estava ativada.

Teste físico de BK encontrou o sintoma esperado da arquitetura antiga: poucos segundos de fala, pausa muito longa antes do próximo pedaço, texto completo sem voz em parte dos turnos e fala parcial quando uma das requisições segmentadas falhava.

## Método anterior

```text
resposta cognitiva completa
→ SpeechSegmenter (~64–100 chars)
→ N SpeechUtterances WEB_SAFARI
→ N requests Fish /v1/tts format=wav
→ Core acumula cada response inteira
→ Safari response.arrayBuffer()
→ decodeAudioData
→ toca segmento N
→ espera/prepara segmento N+1
```

Um erro de transporte em qualquer segmento podia cancelar/suprimir a entrega vocal do restante do mesmo `cognitive_turn_id`, preservando o texto mas produzindo a percepção de que a Amora “parou de falar no meio”.

## Evidência / problema

- segmentação cognitiva e segmentação de transporte estavam acopladas sem necessidade;
- Fish já produz bytes antes da síntese completa terminar;
- o Core já usava `httpx.stream`, mas juntava os chunks novamente antes de responder ao Safari;
- o Safari já continha um player PCM incremental, porém sem rota TTS conectada;
- múltiplas chamadas por resposta aumentavam latência, pontos de falha e risco de gaps audíveis;
- emoção/expressividade Fish existe por tags de texto, mas provider não deve inferir nem possuir o estado emocional da Amora.

## Alternativas consideradas

1. manter WAV segmentado e apenas aumentar `soft_max_chars`;
2. pre-renderizar mais segmentos em paralelo;
3. expor a API key Fish diretamente ao Safari e conectar browser → Fish;
4. usar WebSocket Fish diretamente como novo dono da sessão vocal;
5. manter Core como fronteira e retransmitir PCM incremental de um único turno;
6. aplicar tags emocionais livremente a partir do texto/LLM.

## Decisão

### 1. Unidade de entrega Web = turno cognitivo

Para `WEB_SAFARI`, os `SpeechSegment` continuam existindo como estrutura interna, journal, normalização e rastreabilidade. Eles **não** viram mais uma chamada Fish cada.

Depois de `cognition.respond()` concluir, o Core cria uma única `SpeechUtterance` de entrega Web contendo:

- `plan.speech_text` completo;
- `plan.display_text` completo;
- `cognitive_turn_id`;
- todos os `cognitive_segment_ids`;
- cues agregados;
- `speech_delivery_unit=cognitive_turn`.

Android/local pode preservar a estratégia segmentada quando ela ainda for útil ao backend local.

### 2. Fish normal = PCM incremental pelo Core

Caminho normal:

```text
Safari
→ GET autenticado /v1/web/voice/utterances/{id}/stream
→ Core valida lease/privacy/remote_tts_allowed
→ Fish /v1/tts
   format=pcm
   latency=balanced
   chunk_length=200
→ Core recebe bytes enquanto Fish sintetiza
→ Core retransmite PCM16LE sem DSP cloud
→ Safari ReadableStream
→ PcmStreamPlayer
→ pequeno buffer inicial (~60 ms)
→ playback enquanto os próximos bytes ainda chegam
```

A API key e o `reference_id` continuam exclusivamente no Core/SecretStore. O navegador recebe apenas áudio e metadados bounded.

A rota WAV antiga permanece como compatibilidade para backends sem `StreamingVoiceBackend` e diagnóstico, mas não é o caminho Web cloud preferido.

### 3. Backpressure e delivery truth

O Safari mantém buffer bounded e desacelera a leitura quando já há áudio suficiente agendado. Não baixa a resposta inteira para só depois tocar.

Contrato de entrega continua:

```text
claim
→ primeiro áudio realmente começa a tocar
→ POST started
→ stream termina + todo PCM agendado drena
→ POST ack spoken=true
```

Receber o último byte do servidor não equivale a ter ouvido a fala.

Barge-in, mute, stop e pagehide abortam fetch/player e cancelam a entrega pelo Core.

### 4. Fallback não mistura vozes no meio

Fish pode cair para Gemini somente **antes do primeiro PCM Fish ser emitido**.

Depois que Fish começou a tocar, erro de stream encerra aquela entrega. Não é permitido emendar Gemini no meio da mesma frase, porque isso quebraria identidade e continuidade auditiva.

### 5. Expressão Fish pertence ao renderer; emoção pertence ao Core

Tags Fish são capability de renderização. O Core continua sendo autoridade sobre emoção.

O renderer pode traduzir estado canônico bounded para tags Fish. Mapeamento inicial:

```text
joy             -> [happy]
gratitude       -> [grateful]
relief          -> [relieved]
anger           -> [angry]
irritation      -> [annoyed]
fear            -> [worried]
disappointment  -> [disappointed]
```

A tag emocional só entra quando `source_intensity >= 0.35`.

Cues semânticos já detectados pelo Core podem mapear para tags provider-specific, por exemplo `LAUGHTER -> [chuckling]`, `SIGH -> [sighing]` e `SURPRISE -> [surprised]`.

Essas tags existem somente no texto enviado ao Fish. Não alteram `DisplayText`, memória, journal literal ou conteúdo canônico da resposta.

Prosody Fish pode consumir somente parâmetros bounded já derivados pelo Core; não ganha uma segunda classificação emocional independente.

### 6. Qualidade antes de latência extrema

Usar `latency=balanced`. A meta é remover espera artificial pelo arquivo completo sem forçar um modo agressivo que possa piorar o timbre. Ocasional artefato robótico do modelo/provider deve ser medido separadamente, não compensado automaticamente com pitch/formant/DSP antigo.

## Motivos

- elimina N requests TTS por uma única resposta Web;
- começa playback antes da síntese completa terminar;
- reduz gaps artificiais e pontos de falha;
- aproveita componentes de streaming já previstos na ADR-0019;
- mantém segredo Fish fora do navegador;
- preserva segmentos cognitivos e delivery truth;
- impede troca de timbre Fish→Gemini no meio de uma fala;
- usa emoção causal já existente em vez de prompt/provider como autoridade emocional;
- não reabre FINAL_B/VoiceSignature nem adiciona DSP cloud.

## Consequências

- `SpeechUtterance` Web pode carregar a resposta inteira e precisa de bound maior que os segmentos antigos;
- o stream HTTP precisa de buffering de proxy desabilitado quando possível (`X-Accel-Buffering: no`);
- Safari/OS ainda pode suspender rede ou `AudioContext` quando a página/tela é colocada em background; isso continua gate físico separado;
- falha depois do primeiro PCM pode resultar em fala incompleta, mas não em mistura silenciosa de providers;
- cues/tag Fish precisam de testes e podem ser refinados conforme evidência auditiva real;
- streaming HTTP atual não impede migração futura para WebSocket Fish se a cognição também passar a produzir texto incrementalmente.

## Critério para revisitar

Revisitar quando:

- streaming HTTP não atravessar de forma confiável o ingress/proxy do hosting;
- Safari físico apresentar underruns frequentes mesmo com buffer bounded;
- a Fish exigir WebSocket para atingir latência/estabilidade aceitáveis;
- a resposta cognitiva passar a ser disponibilizada incrementalmente antes do plano completo;
- tags emocionais degradarem identidade/clareza no clone;
- o provider alterar formato, sample rate, limites ou política do endpoint gratuito.

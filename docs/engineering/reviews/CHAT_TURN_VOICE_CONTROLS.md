# Chat por turno e controles vocais Web

Data: 2026-10-25. Base: main `c82e76b5a99179c8e49811a7928d2cf26bf6ab9a`
(PR #61 integrado). Branch: `fix/chat-turns-voice-controls`.
Decisão: [ADR-0019](../../decisions/ADR-0019-turn-display-and-optional-voice-stream.md).

## Causas e correções

| Problema comprovado no código | Correção |
|---|---|
| `queueLocalSpeech` desenhava `item.text`/ID de utterance | Snapshot literal `display_text` por cognitive_turn_id; voz continua segmentada |
| Normalização vocal removia marcadores do texto mostrado | `SpeechPlan` imutável separa display, speech, cues, privacy e turn |
| Risadas/emoji apagados da interpretação vocal | Cues semânticas explícitas; marcadores sem interpretação declarados unsupported |
| Clear apagava imediatamente | Diálogo modal nativo acessível, descrição, Cancelar e confirmar |
| Não havia preferência mute | Chave local separada; texto/fila/histórico continuam sem render/playback context |
| Stop notificava somente um alvo e decode tardio escapava do epoch | Todos os claims notificados, controllers abortados, player parado e guards após decode |
| Claim podia chegar depois do stop | Epoch de claim e supressão por turno, com cancelamento do lease tardio |
| Três renders concorrentes para um único slot do Core | Preparação serial; até três claims; N+1 prepara enquanto N toca |
| Callback tardio de started/ACK podia alterar segmento cancelado | Não reabrir delivery cancelado nem registrar fala ouvida |
| Pagehide deixava activeRecord/fila no estado antigo | Mesmo cleanup integral, compatível com retorno do BFCache |

## Contratos e limites

`SpeechUtterance.text` mantém entrada vocal; `display_text` é aditivo no JSON.
Realização estruturada validada/fallback captura o texto completo antes de
segmentar; todos os segmentos entregam o mesmo snapshot. Uma utterance Web
apenas visual, como 😂, pode ter speech vazio e nunca invoca o renderer. A15
continua exigindo fala não vazia. No upgrade, claims de rows antigas sem snapshot
usam os display fragments cognitivos existentes; não há reconstrução de emoji
perdido no passado nem agrupamento por suposição de histórico local legado.

Web apresenta/salva antes de qualquer pedido TTS. Um ID visual de turno evita
duplicar o snapshot em segmentos, reclaim, reload e falha vocal. Máximo 50 user
e 50 Amora, 100 bolhas, com pruning independente por role e ordem cronológica.
Diagnóstico aparece como status separado. Markdown permitido permanece literal,
com textContent; não há novo parser HTML/script. Histórico não é replay/Memory.

`SpeechPlan.cue_support()` declara cues não suportadas pelo renderer atual;
não injeta tags, sons artificiais ou nomes de emoji na fala. Marcadores sem
semântica implementada ficam em `unsupported_markers`. O snapshot contém o
original, inclusive kkkk/emojis/pontuação. Nenhum parâmetro Supertonic, FINAL_B,
VoiceSignature, Emotion/Prosody ou provider foi alterado.

`cancel` recebe speech_only opcional, false por default. Mute/manual stop/erro de
voz/saída da página usam true; não cancelam a tarefa cognitiva nem apagam texto.
Novo input/barge-in continua usando cancelamento cognitivo existente. Supressão
da entrega persiste no JSON para segmentos tardios com a chave cognitiva
`speech:cognitive:<turn>:<ordinal>`. Reavaliar essa leitura se mudar esse produtor.
Nenhuma migration SQL ou retry de COMMIT foi acrescentado. Falha HTTP de cancel
fica visível e pendente; nova tentativa é da operação idempotente de cancel,
nunca uma repetição de transação de resultado ambíguo dentro do adapter SQL.

Clear confirmado remove somente histórico/bolhas locais. Cancelar não altera
nada. Pairing e mute persistem independentemente em suas chaves anteriores/nova;
forget não limpa conversa. Nenhuma chamada de Memory/Turso é emitida por clear.

## Streaming preparado

`VoiceBackend.render` permanece intacto; `StreamingVoiceBackend` é capacidade
opcional. VoiceSession/VoiceChunk/validated_chunks descrevem sequência PCM16,
identidade, formato, final e fechamento/cancelamento do producer. Sem rota ou
backend streaming ativado, novo provider, OmniVoice ou modelo no Northflank.

`web/audio-player.mjs` isola WAV completo em WavPlayer e oferece PcmStreamPlayer
para Float32 interleaved sequencial, sem arrayBuffer/decode completo. Buffer
bounded/backpressure, buffering/underrun, final após consumo e stop de fontes
agendadas estão cobertos por samples sintéticos. A futura bridge precisa
converter PCM16 e certificar transporte/áudio; scheduling não significa ACK.
Fontes e compatibilidade de API constam na ADR; execução física continua pendente.

## Arquivos

- Core: cognition speech/models/service/hardened/structured_runtime/resilient e
  exports; conversation models/service/integrated/store/routes; voice/backend;
  web/routes (não renderizar cancelados ou conteúdo só visual).
- Browser: app-v2, browser-state, audio-player, index/styles.
- Regressões: test_chat_speech_plan, Safari routing/static, web_turn_voice,
  web_audio_player, web_browser, web_persistence e helper executando o app real.
- CI/smoke: incluir novos módulos/helper JS, verificar que player é servido no
  container; gates de segurança/dependências inalterados.

## Validação automatizada

- `.venv/bin/python -m pytest -o addopts= tests -q`: **418 PASS**, cinco avisos
  preexistentes, zero skips. Inclui remote storage/Hrana/baton/COMMIT ambíguo,
  conversation/A15/ACK, STT, pairing, cloud e security.
- `node --test avatar/tests/*.test.mjs tests/web_*.test.mjs`, Node **22.23.4**:
  **50 PASS**, zero skips. Executa o app entregue com DOM/HTTP/Web Audio simulados;
  persistência, state machine e módulos de player são os de produção.
- Ruff nos módulos novos/plano/contrato e teste: PASS; git diff --check: PASS.
- Regressões específicas (plano/remote storage/Safari/conversation/cancellation/
  predeploy): **120 PASS**, um aviso existente.
- Core completo no container Alpine/musl, non-root/read-only: **418 PASS**;
  `pip check` sem dependências quebradas. Imagem local final:
  `sha256:ac32bd78054ac4fe86bbf80c0fcea7a68f12120958ae937e4bf76c4e7f06a715`.
- `python3 tools/cloud/smoke-container.py amora-core:chat-voice-controls`: **PASS**. Smoke cobre
  non-root, read-only, cap-drop ALL, NNP, /live, /ready, auth/pairing, módulos .mjs,
  fila persistida, restart e SIGTERM. CI/PR/SHA finais são reportados no handoff.
  Nenhum deploy remoto realizado.

## Gate físico ainda aberto

Após revisão/CI e atualização autorizada do staging, verificar no Safari real:

- Uma resposta com três segmentos aparece em uma bolha, com emoji/risada literal.
- Reload/reclaim mantém uma bolha e o limite 50+50; clear pede confirmação.
- Mute persiste ao reabrir, continua texto/STT e não pede /audio.
- Stop durante render/playback impede todo áudio posterior do mesmo turno.
- TTS indisponível deixa texto completo, pairing, /live, /ready e restart válidos.
- Player/WAV, barge-in e AudioContext medidos no iPhone; worker Supertonic real
  continua gate independente. PCM sintético não certifica streaming acústico.

Sem alteração de persona, GDD, Emotion canon, Provider Mesh, identidade vocal,
secrets, tokens, seleção de storage, infra ou branch protection. Nenhuma decisão
de produto em aberto foi escolhida além do pedido explícito desta missão.

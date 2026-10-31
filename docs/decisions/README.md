# AMORA — Architecture Decision Records (ADR)

Este diretório registra **por que** decisões importantes da Amora foram tomadas, rejeitadas ou substituídas.

O objetivo é impedir que chats, agentes ou ciclos futuros restaurem sem querer um método que já falhou ou interpretem uma mudança de roadmap como esquecimento.

## Autoridade

- `docs/canon/*` = verdade atual.
- `docs/roadmap/VERSIONS.md` = destino/versionamento atual.
- `docs/decisions/*` = histórico racional das decisões.
- histórico Git = versões exatas anteriores.

Um ADR antigo não ganha autoridade sobre o canon atual só por existir.

## Status

- `PROPOSED` — ainda em avaliação;
- `ACCEPTED` — decisão vigente;
- `REJECTED` — solução avaliada e descartada;
- `SUPERSEDED` — já foi vigente, mas foi substituída por ADR posterior;
- `DEPRECATED` — caminho antigo mantido temporariamente por compatibilidade.

## Template

```markdown
# ADR-NNNN — Título

Status: ACCEPTED
Data: YYYY-MM-DD
Supersedes: ADR-NNNN / none
Superseded by: ADR-NNNN / none

## Contexto
...

## Método anterior
...

## Evidência / problema
...

## Alternativas consideradas
...

## Decisão
...

## Motivos
...

## Consequências
...

## Critério para revisitar
...
```

## Índice

- `ADR-0026-provider-authority-and-expression-protocol.md` — separa Constitution/LivingProfile/TurnFrame/RealizationContract da conversa literal; Groq/OpenRouter usam Responses `instructions`, Gemini usa `systemInstruction`; regionalidade/palavrão são projeção contextual do Core.
- `ADR-0025-corroborated-local-relationship-learning.md` — Relationship Graph aprende apenas após corroboração local de eventos independentes; provider nunca ganha autoridade direta sobre identidade social.
- `ADR-0024-portable-avatar-bundle.md` — bundle portátil v1 do Avatar com projeto + assets + manifest/hashes; distinto do backup operacional do Core.
- `ADR-0023-fish-voice-stability-and-first-audio-telemetry.md` — sampling Fish mais consistente, tags expressivas bounded, payload stream/WAV unificado e telemetria de primeiro áudio.
- `ADR-0022-turn-level-fish-pcm-streaming-and-expression-tags.md` — Safari entrega um fluxo PCM por turno cognitivo; Fish toca enquanto sintetiza; emoção/cues do Core viram tags bounded do renderer.
- `ADR-0021-fish-cloud-voice-primary-for-web.md` — Fish `s2.1-pro-free` + clone da Amora como voz Web primária; Gemini fallback explícito; Supertonic deixa de ser default da Web.
- `ADR-0020-libsql-session-recovery-and-usage-boundary.md` — recovery de sessão após operação/transação falha, sem retry ambíguo; telemetria de usage degradável preservando realização válida.
- `ADR-0019-turn-display-and-optional-voice-stream.md` — bolha por turno, DisplayText/SpeechPlan, mute/stop vocal, confirmação de limpeza e streaming PCM opcional sem novo renderer.
- `ADR-0018-private-web-surface-and-browser-continuity.md` — target explícito Web 1:1, journal idempotente em SQL, credencial Web durável limitada e histórico visual 50+50.
- `ADR-0017-core-runtime-base.md` — base Core/Web Alpine validada sobre musl; gate HIGH/CRITICAL preservado, staging separado.
- `ADR-0016-public-cloud-security-boundary.md` — fronteira de segurança cloud, browser roles, auth/origins/limits e pré-deploy fail-closed.
- `ADR-0015-portable-core-storage-and-voice.md` — separação Core/Web, storage e voice backend para hosting remoto substituível.
- `ADR-0014-web-local-amora-voice-worker.md` — implementação local de voz/assinatura compartilhada com o worker Web, hoje preservada como caminho opt-in/laboratório.
- `ADR-0013-web-safari-primary-iteration-surface.md` — Safari/Web como superfície primária de iteração sem mover estado canônico ao navegador.
- `ADR-0012-hybrid-spoken-cognition.md` — integração de cognição/fala e contratos de delivery preservados nas evoluções posteriores.
- `ADR-0011-accept-amora-voice-signature-v1-soft.md` — `AMORA_VOICE_SIGNATURE_V1_SOFT` aceita e congelada como assinatura de laboratório.
- `ADR-0010-freeze-amora-child-final-b-base.md` — `AMORA_CHILD_FINAL_B_PLUS60` congelada como base aprovada de laboratório.
- `ADR-0009-ready-tts-voice-signature.md` — backend TTS local pronto + `VoiceSignature` versionada; treino próprio deixa de ser requisito da V1.
- `ADR-0008-canonical-voice-training-strategy.md` — **SUPERSEDED**; registrava treino/fine-tuning próprio como caminho canônico.
- `ADR-0007-emotion-loop-and-test-isolation.md` — conversa/ações alimentam Emotion Engine em loop fechado; testes podem executar sem contaminar memória/relação/mood persistente.
- `ADR-0006-structured-dialogue-realization.md` — `DialoguePlan` vira `RealizationFrame` estruturado e validado; prompt/blacklist não são enforcement suficiente.
- `ADR-0005-roadmap-split-v09-v11-v12.md` — escopo antigo V0.9/V0.95 dividido entre V1.1 e V1.2.
- `ADR-0004-zero-cost-provider-mesh.md` — objetivo R$ 0, Groq/Cloudflare, OpenRouter especialista/último recurso.
- `ADR-0003-local-dialogue-semantic-authority.md` — Core decide semântica social; provider realiza linguagem.
- `ADR-0002-local-voice-as-normal-path.md` — voz normal local pt-BR no estágio histórico correspondente; refinado/superseded operacionalmente pelos ADRs de voz Web posteriores.
- `ADR-0001-document-authority-and-versioning.md` — canon no GitHub + ADR + roadmap; documentos antigos não são especificação atual.

## Nota de numeração

Em 2026-10-31 a auditoria estrutural encontrou dois documentos distintos usando `ADR-0022`. O ADR Fish manteve o número original e o Portable Avatar Bundle foi renumerado para `ADR-0024`. A mudança é apenas documental e elimina ambiguidade de referência; não altera runtime nem decisão técnica.

## Regra de uso

Antes de reintroduzir tecnologia/método já usado anteriormente:

1. procurar ADR relacionado;
2. verificar se está `REJECTED` ou `SUPERSEDED`;
3. entender o problema que motivou a troca;
4. só reabrir a decisão se houver evidência nova que mude as premissas.

Não repetir experimento antigo apenas porque o chat atual não lembra dele.

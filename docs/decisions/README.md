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

- `ADR-0001-document-authority-and-versioning.md` — canon no GitHub + ADR + roadmap; documentos antigos não são especificação atual.
- `ADR-0002-local-voice-as-normal-path.md` — voz normal local pt-BR; Gemini TTS removido do caminho normal.
- `ADR-0003-local-dialogue-semantic-authority.md` — Core decide semântica social; provider realiza linguagem.
- `ADR-0004-zero-cost-provider-mesh.md` — objetivo R$ 0, Groq/Cloudflare, OpenRouter especialista/último recurso.
- `ADR-0005-roadmap-split-v09-v11-v12.md` — escopo antigo V0.9/V0.95 dividido entre V1.1 e V1.2.
- `ADR-0006-structured-dialogue-realization.md` — `DialoguePlan` vira `RealizationFrame` estruturado e validado; prompt/blacklist não são enforcement suficiente.
- `ADR-0007-emotion-loop-and-test-isolation.md` — conversa/ações alimentam Emotion Engine em loop fechado; testes podem executar sem contaminar memória/relação/mood persistente.

## Regra de uso

Antes de reintroduzir tecnologia/método já usado anteriormente:

1. procurar ADR relacionado;
2. verificar se está `REJECTED` ou `SUPERSEDED`;
3. entender o problema que motivou a troca;
4. só reabrir a decisão se houver evidência nova que mude as premissas.

Não repetir experimento antigo apenas porque o chat atual não lembra dele.

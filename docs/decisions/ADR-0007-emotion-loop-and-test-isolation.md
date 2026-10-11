# ADR-0007 — Emoção em loop fechado e isolamento de testes

**Status:** ACCEPTED  
**Data:** 2026-10-11  
**Supersedes:** uso parcial do Emotion Engine apenas por chamadas explícitas/isoladas  
**Superseded by:** none

## Contexto

O Emotion Engine V0.5/V1 já implementa appraisal, Emotion Events, Global Mood, Entity Affect, decay, blame, intentionality, spillover e Social Bids. Porém o fluxo normal de conversa lê mood/affect com mais frequência do que produz appraisals a partir de eventos reais. Isso pode manter `mood_valence` em baseline durante conversas prolongadas apesar de o restante da arquitetura supor emoção ativa.

Testes técnicos repetidos também podem contaminar memória, relações e estado emocional persistente se atravessarem o mesmo pipeline sem metadata de isolamento.

## Decisão

A V1 exige **loop emocional fechado**:

```text
evento real
↓
appraisal bounded/auditável
↓
Emotion Engine
↓
mood + affect + Emotion Event
↓
Dialogue / Autonomy / Avatar / Prosody
↓
ação/fala
↓
resultado observado
↓
novo evento
```

Eventos normais de conversa e ação devem poder produzir appraisal sem depender de comando manual/API de debug.

Ao mesmo tempo, turnos/eventos marcados como teste devem permitir executar o pipeline sem persistir aprendizado não desejado.

## Contrato de teste

Metadata equivalente a:

```text
test_mode=true
memory_consolidation=false
relationship_learning=false
emotion_persistence=false
```

Quando `emotion_persistence=false`, o sistema pode calcular appraisal/resultado efêmero para teste, mas não deve alterar mood/affect persistente.

## Separação de responsabilidades

- Emotion Engine representa estado interno e causalidade;
- Dialogue Orchestrator traduz estado interno em comportamento social;
- Prosody Planner traduz uma parte pequena do estado/intenção em expressão vocal bounded;
- emoção não controla pitch/volume diretamente como atalho;
- provider não é autoridade sobre estado emocional canônico.

## Alternativas rejeitadas

- manter emoção apenas como endpoint/CLI/test fixture;
- pedir ao LLM para "agir bravo/triste" sem estado local causal;
- mapear emoção diretamente para volume/pitch;
- reset completo de memória após toda bateria de teste.

## Consequências

Precisamos adicionar uma ponte de appraisal para eventos reais, telemetria do último appraisal/evento e flags de persistência nos fluxos de teste.

Limpeza de dados de desenvolvimento já contaminados deve ser seletiva, preservando self-model, canon e relações válidas.

## Critério de saída

- conversas reais conseguem mover mood/affect quando há evento significativo;
- decay continua determinístico;
- diálogo recebe estado emocional atualizado;
- teste técnico marcado não persiste memória/relação/mood quando desabilitado;
- observabilidade explica por que o mood mudou ou permaneceu neutro.

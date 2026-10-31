# PROJECT_STATE — Amora-Chibi

**Última atualização:** 2026-10-31  
**Base operacional:** `main` após PR #85 (`90ddd0869e8af55ef02f29b00f8a652120ee19dc`)  
**Objetivo:** checkpoint operacional curto; para a auditoria estrutural detalhada ler `docs/engineering/reviews/PROJECT_REMAP_2026-10-31.md`.

## 1. Autoridade

Ordem de leitura:

1. `docs/canon/SKILL.md`;
2. `docs/canon/ARCHITECTURE.md`;
3. `docs/canon/GDD.md`;
4. `docs/canon/DIALOGUE_EMOTION.md`;
5. `docs/canon/PROVIDER_MESH.md`;
6. `docs/canon/VOICE_IDENTITY.md`;
7. `docs/roadmap/VERSIONS.md`;
8. ADRs relevantes;
9. `docs/engineering/reviews/PROJECT_REMAP_2026-10-31.md`;
10. este snapshot;
11. código/testes/CI atuais.

Quando um documento antigo contradizer o código + ADR mais recente, não restaurar silenciosamente o método antigo.

## 2. Decisão operacional atual: visual congelado

BK congelou temporariamente a frente visual para priorizar wiring/consolidação do cérebro já implementado.

**Congelado por enquanto, não cancelado:**

- integração visual final do Avatar na superfície conversacional;
- visual perception;
- screen mapping;
- OCR/keyframes/scene-change perception;
- Android visual/Living Console;
- V1.2 estética/self-image/roupa/aparência.

Não abrir implementação visual até BK reabrir explicitamente essa frente.

Isso não altera o roadmap canônico: é prioridade de execução atual, não remoção de feature.

## 3. Runtime operacional atual

### Core/Web

- Core + Web/PWA rodam em container Linux remoto.
- Safari/Web permanece a superfície conversacional principal de iteração.
- Android atual é cliente legado funcional, não o cliente final.
- `WEB_SAFARI != DEVICE_A15` permanece invariante.

### Persistência

- `StorageBackend` suporta SQLite local e libSQL remoto.
- O caminho cloud usa storage remoto libSQL.
- SQLite local permanece válido para desenvolvimento/offline; não remover.
- transactions/recovery/atomic writes remotos estão endurecidos.
- migrations são histórico de schema necessário, não duplicação descartável.

### Voz Web

Caminho normal atual:

```text
WEB_SAFARI
→ Core
→ Fish Audio s2.1-pro-free + clone da Amora
→ PCM s16le incremental por turno cognitivo
→ Safari PcmStreamPlayer
```

Fallback:

```text
Fish falha antes do primeiro PCM
→ Gemini TTS explícito
```

Depois do primeiro PCM Fish não misturar Gemini na mesma fala.

Supertonic/FINAL_B/VoiceSignature e RemoteVoiceBackend continuam preservados como laboratório/offline/opt-in, não default Web.

## 4. Wiring cognitivo/autônomo fechado após o remapeamento

### Scheduler + autonomia idle — ATIVO / INTEGRADO

PR #82 adicionou lifecycle assíncrono real ao `Scheduler` dentro do lifespan da API.

Fluxo normal:

```text
API lifespan
→ SchedulerRuntime
→ AutonomyEngine.schedule_idle()
→ Scheduler.run_due()
→ autonomy.cycle(runtime_auto_execute=true)
```

Regras importantes:

- job idle respeita atividade de fala/entrega;
- falha isolada de job não mata o scheduler;
- shutdown cancela/join o runner e drena jobs em memória;
- ciclo manual/debug continua manual.

### Autonomia → ActionEngine — ATIVO / INTEGRADO / BOUNDED

PR #82 também fechou:

```text
AutonomyEngine
→ autonomy.decision
→ ActionEngine
→ planner
→ permission/privacy/cost/risk/retry/effect verification
→ execution result
```

Somente decisão de runtime explicitamente marcada e com outcome `SELECTED` pode autoexecutar.

`KEPT_CURRENT`, `DEFERRED`, `AUTH` e `SECRET` não atravessam automaticamente para execução.

O wiring reutiliza os gates existentes do ActionEngine; não existe bypass autônomo especial.

### ACIR + Perspective no caminho normal — ATIVO / INTEGRADO

PR #83 integrou ACIR no `ContextCompiler`, depois da resolução social do turno e antes do provider.

Fluxo normal:

```text
texto/STT
→ social/addressee
→ CognitiveRequest final
→ PerspectiveResolver
→ ACIR local
→ DialoguePlan
→ provider
```

A integração é deliberadamente conservadora:

- utterance comum recebe envelope ACIR com speaker/addressee canônicos + conteúdo bruto;
- não existe parser semântico geral de texto livre e nenhum foi inventado;
- `AUTH`/`SECRET` não constroem/exportam ACIR;
- objetivo interno de iniciação autônoma não é rotulado falsamente como utterance recebida.

### Relationship learning — ATIVO / BOUNDED

PR #84 fechou o ciclo de aprendizagem relacional local.

O provider nunca escreve diretamente na Relationship Graph.

Fluxo:

```text
provider/reflection
→ proposal não canônica
→ WorldClaim HYPOTHESIS
→ RelationshipLearningService
→ corroboração local
→ RelationshipEvolutionService
→ Relationship Graph temporal
```

Política vigente está registrada em `ADR-0025-corroborated-local-relationship-learning.md`.

Gates atuais:

- predicados automáticos inicialmente limitados a `father_of`, `mother_of`, `child_of`;
- endpoints precisam ser entidades canônicas;
- endpoints precisam pertencer ao par social do input observado;
- somente input `stt`/`text` autoriza learning;
- `test_mode=true` e `relationship_learning=false` bloqueiam mutação;
- resposta gerada pela própria Amora não pode reabrir um turno bloqueado;
- mínimo de 2 IDs de evento observados distintos;
- confiança média mínima 0.78;
- relação existente mais forte nunca é rebaixada;
- provenance registra eventos, hipóteses e confiança;
- relações aprendidas são reidratadas no `EntityRegistry` após restart.

Directed affect (`affection`, `trust`, `anger`, `irritation`, `fear`, `gratitude`) continua pertencendo ao Emotion Engine e não é confundido com relação estrutural.

### PerspectiveConsolidator — ATIVO / INTEGRADO EM REFLECTION ESTRUTURADA

PR #85 removeu o write direto de `BeliefHypothesisProposal` para `WorldModel`.

Agora:

```text
structured reflection proposal
→ typed semantic ACIR
→ PerspectiveResolver
→ PerspectiveConsolidator
→ WorldModel HYPOTHESIS
```

Regras:

- subject precisa ser entidade canônica ancorada no ACIR do input;
- object que for entidade canônica também precisa estar ancorado na perspectiva atual;
- subject desconhecido, terceiro fora da perspectiva ou referência não resolvida fica `DEFERRED`;
- hipótese aceita permanece não canônica e mantém TTL/gates do World Model;
- evidence, privacy, provider/model, turn e source entity são preservados;
- hipóteses relacionais consolidadas continuam alimentando o corroboration gate do PR #84.

## 5. Módulos realmente ativos

Ativos no runtime normal ou em contratos operacionais usados:

- API/auth/pairing/security;
- Event Bus;
- identity/device/session/secrets;
- storage SQLite/libSQL, migrations e backup;
- Memory retrieval e reflection gated;
- World Model e Self Model;
- Addressee/Social Scene;
- ACIR/Perspective no contexto cognitivo normal;
- PerspectiveConsolidator em reflexão estruturada;
- Relationship learning bounded;
- Emotion Engine + Interaction Appraiser;
- Scheduler runtime + Autonomy Engine idle lifecycle;
- Autonomy → ActionEngine bounded;
- Conversation + DialoguePlan + RealizationFrame validator;
- provider mesh;
- STT Groq;
- Web/PWA atual;
- Fish/Gemini voice chain;
- Avatar Core/Studio — motor existente, mas frente visual congelada;
- observability/operations/CI/container.

## 6. Implementado mas ainda não vivendo completamente

### Speaker tracking

`SessionSpeakerTracker` estabiliza labels de diarização por sessão e pode aceitar match contextual de pessoa conhecida.

Ele **não executa diarização sozinho**.

As superfícies atuais ainda usam principalmente speaker declarado; não há upstream normal fornecendo diarization labels reais ao Core.

**Status:** PRONTO NÃO INTEGRADO. Não fingir que diarização/voice identification já existe.

### Core EnergyVad

Existe, mas o caminho Web atual usa VAD da superfície e não foi encontrado consumidor normal do `core.vad`.

**Status:** DORMENTE/LEGADO. Não remover até definir plataforma consumidora.

### Avatar runtime no produto

Avatar Core/Studio/rig/assets/IK/physics/expressions/animações e portable ZIP são reais.

O runtime visual ainda não é a presença normal dentro da superfície de conversa.

**Status:** PARCIAL, porém frente visual congelada por decisão atual de BK.

## 7. Dívida / duplicação

### Cognição

Caminho vigente continua uma pilha evolutiva:

```text
service
→ hardened
→ resilient
→ production
→ structured_runtime
```

Além de pares `context/context_v1`, `store/store_v1` e gerações de providers.

Não apagar bases isoladamente: a implementação final ainda herda comportamento.

Refactor correto:

```text
identificar comportamento vigente
→ cobrir com replay/regressões
→ achatar implementação
→ remover apenas base realmente sem consumidor
```

Nada de faxina nuclear.

### Web legado

`web/app.js` antigo não é carregado pelo HTML atual e não foram encontradas referências normais. Ele usa `speechSynthesis` superseded.

**Status:** MORTO / candidato forte a remoção.

Como a frente Web/visual está congelada, não priorizar essa limpeza agora sem decisão específica.

### ADR duplicado — RESOLVIDO

A duplicação antiga de ADR-0022 foi corrigida no PR #81:

- ADR-0022 — Fish streaming;
- ADR-0023 — Fish stability/TTFS;
- ADR-0024 — Portable Avatar Bundle.

ADR-0025 registra agora a política de relationship learning local corroborado.

## 8. Não implementado / não integrado ainda

### Visual — congelado

- visual perception;
- screen mapping;
- OCR/keyframes/scene changes;
- presença visual final na superfície principal;
- Android Living Console visual;
- V1.2 estética/self-image.

### Não visual restante

- integração de `SessionSpeakerTracker` depende de uma fonte real de diarization labels; o Core já sabe estabilizá-las, mas não deve fabricar diarização;
- plugins externos reais continuam backlog e devem seguir roadmap/canon antes de implementação;
- flatten da pilha cognitiva continua dívida técnica e exige replay/regressões antes de remoção de bases;
- observabilidade e testes devem acompanhar qualquer nova integração.

## 9. Próxima ordem recomendada enquanto o visual estiver congelado

```text
P0 — concluído
✓ Scheduler lifecycle + autonomia idle
✓ autonomia → ActionEngine bounded
✓ ACIR/Perspective no caminho normal
✓ relationship learning + test isolation efetivo
✓ PerspectiveConsolidator em reflection estruturada

P1 — não visual
1. manter/expandir regressões dos loops recém-integrados
2. planejar flatten da cognição com replay antes de qualquer remoção
3. integrar SpeakerTracker somente quando existir diarization label real da superfície
4. avaliar plugins externos conforme roadmap/canon, sem acoplamento precoce
5. limpeza de código morto apenas fora de área congelada ou quando explicitamente autorizada

FROZEN
- percepção/presença visual
- Android visual/Living Console
- V1.2 estética/self-image
```

Regra atual: **conectar e consolidar o cérebro existente; não abrir visual até BK reabrir a frente.**

## 10. Últimos merges relevantes

- PR #79 — Portable Avatar ZIP bundle — merge `693538453a17e3eaf05b16543be01a3a68365647`.
- PR #80 — Fish voice stability + first-audio telemetry — merge `ca8fcf6d803657d99f323573cf482eb2d830a8a1`.
- PR #81 — runtime remap + PROJECT_STATE + correção de numeração ADR — merge `0071aa6b5cf17fd5e8a96164f0c2215032554f43`.
- PR #82 — Scheduler lifecycle + autonomia → Actions — merge `95920dc64e30fa108c8cbc44ad821650355a9054`.
- PR #83 — ACIR/Perspective no runtime cognitivo — merge `8da4501ee50d05c355334ea1be1354206ff43f6f`.
- PR #84 — relationship learning corroborado + gates reais — merge `435b2f0245eebf18cab738fe511209dfb17b2c8a`.
- PR #85 — PerspectiveConsolidator no fluxo de reflexão estruturada — merge `90ddd0869e8af55ef02f29b00f8a652120ee19dc`.

CI dos PRs #82–#85 passou em Core Python 3.11–3.14, pre-deploy security e cloud container contracts, incluindo regressões completas no container non-root/read-only.

Esses resultados certificam implementação/CI. Nenhum novo gate físico de Galaxy A15 foi declarado concluído por inferência.

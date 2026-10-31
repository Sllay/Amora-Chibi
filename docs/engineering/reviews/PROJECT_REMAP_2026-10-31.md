# AMORA — Remapeamento estrutural do projeto

**Data:** 2026-10-31  
**Base auditada:** `main` em `ca8fcf6d803657d99f323573cf482eb2d830a8a1`  
**Escopo:** runtime, integração, dívida arquitetural, legado, módulos dormentes e próximos gates.

## 1. Objetivo

Este documento responde uma pergunta diferente de “o código compila?” ou “há testes?”:

> Quais capacidades da Amora realmente participam do runtime normal hoje, quais estão prontas mas sem integração, quais são parciais, quais são legado proposital e quais são código morto?

A classificação foi feita cruzando:

1. canon/roadmap/ADRs;
2. `AmoraCore.bootstrap()` e montagem da API;
3. imports, subscriptions, rotas e consumidores reais;
4. testes/CI;
5. deploy e evidência física conhecida;
6. buscas de referências no código.

CI verde prova regressão automatizada; não prova integração de produto nem gate físico.

## 2. Legenda

| Status | Significado |
|---|---|
| **ATIVO** | participa do caminho normal atual ou de um contrato operacional realmente usado |
| **PARCIAL** | parte relevante funciona, mas falta integração ou comportamento previsto |
| **PRONTO NÃO INTEGRADO** | implementação e testes existem, porém o runtime normal não a aciona |
| **LEGADO PRESERVADO** | não é caminho normal, mas permanece conscientemente por compatibilidade, laboratório, offline ou histórico técnico |
| **DÍVIDA / DUPLICAÇÃO** | caminhos/versionamento acumulados que aumentam complexidade, mas não podem ser apagados sem refactor guiado por testes |
| **MORTO** | sem consumidor normal/referência útil encontrada e substituído pelo caminho vigente |
| **NÃO IMPLEMENTADO** | roadmap/canon prevê, mas o código correspondente ainda não existe |
| **INCERTO** | evidência atual insuficiente; não remover até confirmar |

## 3. Resumo executivo

A Amora possui um Core muito mais completo do que a experiência atual faz parecer. Os maiores problemas encontrados não são “módulos falsos”, e sim **últimas conexões ausentes entre subsistemas já implementados**.

Os quatro gaps estruturais mais importantes são:

1. **autonomia sem relógio de produção:** `AutonomyEngine.schedule_idle()` existe, mas o runtime não chama `schedule_idle()` nem mantém um loop de `Scheduler.run_due()`;
2. **autonomia sem execução automática:** `AutonomyEngine.cycle()` pode escolher e persistir intenção, mas `autonomy.decision` não tem consumidor que leve a intenção automaticamente ao `ActionEngine`;
3. **ACIR/perspectiva fora do caminho conversacional normal:** `ACIRCompiler` e `PerspectiveResolver` existem, mas a conversa Web normal não compila ACIR antes da cognição;
4. **relações evolutivas sem aprendizagem normal:** `RelationshipEvolutionService` existe e relações iniciais são seedadas, porém não foi encontrado um caminho normal que converta conversa/appraisal/reflection em atualização relacional. `relationship_learning` é propagado como contrato, mas não controla uma mutação relacional real.

Esses gaps explicam por que algumas capacidades parecem “prontas” no repo, mas não se manifestam espontaneamente na Amora.

## 4. Matriz de módulos

| Área | Status | Integração real atual | Gap / observação |
|---|---|---|---|
| Core bootstrap/lifecycle | **ATIVO** | `core/amora/app.py` instancia e fecha os subsistemas principais | `app.py` ainda concentra wiring demais |
| API/auth/pairing/security | **ATIVO** | rotas, browser roles, limites, readiness/liveness | manter fail-closed e observabilidade |
| Event Bus | **ATIVO** | cognição, emoção, conversa, ações e avatar publicam/consomem eventos | subscriptions ainda são montadas manualmente no bootstrap |
| TimeService/Clock | **ATIVO** | timestamps, decay, stores, testes simulados | scheduler não ganhou lifecycle de produção |
| Scheduler | **PRONTO NÃO INTEGRADO** | implementação/testes existem | não há loop normal chamando `run_due()` |
| Identity / Entity Registry | **ATIVO** | BK/aliases, dispositivos, sessões e relações iniciais | normal |
| Device/session identity | **ATIVO** | `DEVICE_A15`, `WEB_SAFARI`, pairing e sessions separados | Android precisa rework para Living Console |
| Secrets | **ATIVO** | providers/storage/voice usam SecretStore/env seguros | normal |
| Storage SQLite | **LEGADO PRESERVADO / ATIVO DEV** | dev/offline/backup | não remover |
| Storage libSQL remoto | **ATIVO** | runtime cloud, transactions/recovery/telemetria | latência foi otimizada; continuar observável |
| Migrations | **ATIVO / HISTÓRICO NECESSÁRIO** | schema incremental | não tratar arquivos versionados como duplicação |
| Backup Core | **ATIVO** | DB + sidecar de assets Avatar | distinto do bundle Studio |
| Memory retrieval | **ATIVO** | Context Compiler consulta memória | normal |
| Memory reflection/consolidation | **ATIVO, GATED** | `CognitiveEngine.reflect()` aceita propostas e grava memória validada | desligado em `test_mode`; laboratório Web pode parecer “sem memória” por design |
| World Model | **ATIVO** | claims/contexto/reflection hypotheses | normal |
| Self Model | **ATIVO** | identidade canônica e contexto | normal |
| Relationship registry seed | **ATIVO** | relações iniciais são persistidas/seedadas | não equivale a aprendizagem |
| RelationshipEvolutionService | **PRONTO NÃO INTEGRADO** | serviço/store existem | conversa normal não chama evolução relacional |
| `relationship_learning` | **PARCIAL** | flag aparece no contrato/test mode | nenhum consumidor de mutação relacional encontrado |
| PerspectiveConsolidator | **PRONTO NÃO INTEGRADO** | instanciado no Core/testado | sem chamada normal de runtime encontrada |
| Addressee Engine | **ATIVO** | `/v1/web/utterance` passa por `LanguageSocialPipeline` | manter continuity bounded |
| Social Scene | **ATIVO** | cena/addressee alimentam conversa/contexto | normal |
| PerspectiveResolver | **PARCIAL** | implementado e disponível por API/ACIR | não faz parte do caminho Web normal completo |
| ACIRCompiler | **PRONTO NÃO INTEGRADO** | instanciado/testado | normal conversation não usa `core.acir` antes de provider/cognição |
| Groq Whisper STT | **ATIVO** | Web envia WAV ao contrato STT | normal |
| Browser VAD/barge-in | **ATIVO** | `app-v2.js` controla fala/interrupção local | normal |
| Core `EnergyVad` | **DORMENTE / LEGADO** | instanciado no Core | nenhuma leitura normal encontrada no caminho Web atual |
| SessionSpeakerTracker | **PRONTO NÃO INTEGRADO** | implementação/factory existem | factory não possui consumidor normal; diarização real ainda não existe |
| Emotion Engine | **ATIVO** | conversa passa por `InteractionAppraiser`, mood/affect/bids persistem | um dos loops realmente fechados |
| Dialogue Orchestrator | **ATIVO** | ato/stance/closing/grounding local | normal |
| RealizationFrame/validator | **ATIVO** | structured runtime valida saída antes da fala | normal |
| Model Router | **ATIVO** | providers registráveis/substituíveis | normal |
| Groq cognition | **ATIVO** | provider normal quando disponível | provider não é identidade |
| Cloudflare cognition | **ATIVO CONDICIONAL** | só registra com zero-cost confirmado | correto fail-closed |
| Gemini cognition | **ATIVO FALLBACK** | fallback independente | manter separado do TTS normal |
| OpenRouter | **ATIVO CONDICIONAL** | somente rota explicitamente free | correto fail-closed |
| Cognition runtime | **ATIVO** | `structured_runtime.CognitiveEngine` é export vigente | cadeia de herança acumulou dívida |
| Cognitive Replay | **ATIVO / FERRAMENTA** | rota e sandbox sem mutação | não é caminho normal, mas útil e intencional |
| Reflection | **ATIVO GATED** | normal mode + remote allowed + privacy segura | test mode desliga por design |
| Cognition action suggestions | **ATIVO ATÉ BDI** | evento `cognition.action_suggested` chega à autonomia | depois da decisão falta execução automática |
| Autonomy drives/BDI/utility | **ATIVO QUANDO INVOCADO** | engine/store/testes reais | não roda sozinho no runtime |
| Autonomy idle cycle | **PRONTO NÃO INTEGRADO** | `schedule_idle()` pronto | sem scheduler lifecycle |
| ActionEngine | **ATIVO COMO INFRA** | planner, risk/cost gates, retries, effect verification, API/CLI | não recebe automaticamente intenção da autonomia |
| Skills core | **ATIVO COMO INFRA** | `core.semantic_intention` e `core.rest` | só skills built-in atuais |
| Plugins externos | **NÃO IMPLEMENTADO** | nenhum plugin externo normal registrado | Discord/browser/games/OBS continuam backlog |
| Conversation queue/delivery | **ATIVO** | claim/started/ack/cancel por dispositivo | normal |
| Web/PWA `app-v2.js` | **ATIVO** | superfície conversacional principal | nome “v2” vira dívida depois de remover legado |
| `web/app.js` | **MORTO** | não é carregado pelo HTML vigente; sem referências encontradas | contém `speechSynthesis` antigo; candidato forte a remoção |
| realtime preview | **ATIVO** | preview efêmero; canonical reply continua Core | não falar preview bruto |
| Fish cloud TTS | **ATIVO** | primário Web, PCM incremental por turno | tuning #80 já fechado; só gate auditivo contínuo |
| Gemini TTS | **ATIVO FALLBACK EXPLÍCITO** | somente antes do primeiro PCM Fish | identidade pode mudar; observável |
| Supertonic/FINAL_B/Signature | **LEGADO PRESERVADO** | opt-in local/Android/lab | congelado; não apagar nem retunar sem evidência |
| Remote VoiceBackend/worker | **LEGADO PRESERVADO** | contrato substituível | não é default atual |
| TTFS Fish backend | **ATIVO** | headers/first-byte/first-PCM disponíveis no state | UI Web ainda não exibe esses campos |
| Avatar Core engine | **ATIVO** | project/store/emotion/conversation/social | normal |
| Avatar Studio | **ATIVO** | editor, rig, assets, expressões/animações | normal |
| Avatar portable ZIP | **ATIVO** | projeto + assets + hashes + restore | ADR está com número duplicado; corrigir docs |
| Avatar runtime | **PARCIAL** | `/avatar/?mode=runtime` e state existem | ainda separado da superfície conversacional principal |
| Mind → Avatar básico | **ATIVO/PARCIAL** | emoção/talk/semantic gestures chegam ao Avatar | corpo/presença final é V1.1 |
| Visual perception | **NÃO IMPLEMENTADO** | nenhuma pipeline Core encontrada | V1.1 |
| Screen mapping | **NÃO IMPLEMENTADO** | nenhuma implementação encontrada | V1.1 |
| OCR/scene change perception | **NÃO IMPLEMENTADO** | nenhuma implementação encontrada | V1.1 |
| Android debug client | **LEGADO FUNCIONAL** | pairing/CoreClient/audio/VoiceLab reais | UX/config ainda assumem Core local/Faber; não é cliente final |
| Android Living Console | **NÃO IMPLEMENTADO COMO PRODUTO** | componentes antigos são fundação | precisa rework intencional, não patch da MainActivity antiga |
| V1.2 estética/self-image | **NÃO IMPLEMENTADO** | roadmap apenas | não confundir com color slots do Avatar Studio |
| Observability/operations | **ATIVO** | health/readiness/operations/timings | consolidar painel humano depois |
| CI/cloud container/security | **ATIVO** | Python 3.11–3.14, container, security | CI != experiência física |

## 5. “Lixo eletrônico” confirmado ou forte candidato

### 5.1 `web/app.js` — remover

O HTML atual carrega `app-v2.js`, `fetch-resilience.js` e `realtime-preview.js`. Não foi encontrada referência ao antigo `web/app.js`. O arquivo preserva a implementação Web antiga e usa `window.speechSynthesis`, caminho já superseded pelo transporte de voz atual.

Classificação: **MORTO**.

A remoção deve ser feita em PR próprio ou junto de uma limpeza Web pequena, com Node/browser regressions para provar que nenhum teste/build dependia do arquivo por glob.

### 5.2 Não chamar de lixo apenas porque parece antigo

**Não remover:**

- migrations antigas;
- SQLite local;
- Supertonic/FINAL_B/VoiceSignature;
- Android JNI/VoiceLab enquanto canon preservar laboratório/offline;
- `RemoteVoiceBackend`;
- documentos V0.* históricos;
- camadas base da cognição apenas por terem nomes antigos.

Esses itens ainda possuem função de compatibilidade, histórico, opt-in ou herança usada pela implementação vigente.

## 6. Duplicação e dívida arquitetural

### 6.1 Cognição em camadas versionadas

A implementação exportada hoje é `structured_runtime.CognitiveEngine`, construída por herança sobre:

```text
service.CognitiveEngine
  ↓
hardened.CognitiveEngine
  ↓
resilient.CognitiveEngine
  ↓
production.CognitiveEngine
  ↓
structured_runtime.CognitiveEngine
```

Também existem pares/gerações como `context.py` + `context_v1.py`, `store.py` + `store_v1.py` e adapters de provider evoluídos.

Isto é **DÍVIDA / DUPLICAÇÃO**, não “apagar os arquivos velhos”. A classe final ainda herda helpers/estado das bases. O refactor correto é achatar o comportamento vigente gradualmente, com replay e regressões, e só então eliminar bases sem consumidores.

### 6.2 ADR duplicado

Existem dois documentos diferentes chamados **ADR-0022**:

- Fish PCM por turno;
- Portable Avatar Bundle.

Isso torna referências arquiteturais ambíguas. O ADR Fish deve manter 0022; o bundle Avatar deve ser renumerado para **ADR-0024**, porque 0023 já pertence à estabilização Fish e 0024 está livre.

### 6.3 Drift de documentação

`PROJECT_STATE.md` e partes do roadmap/canon ainda descrevem Termux/Supertonic como voz Web normal e Koyeb/Turso/Modal como próximos candidatos. O runtime comprovado atual já usa container em Northflank, storage libSQL remoto e Fish cloud como voz Web primária.

Histórico não deve ser apagado, mas **snapshot/canon atual não pode continuar descrevendo arquitetura antiga como vigente**.

## 7. Capacidades prontas que “não vivem” ainda

### 7.1 Autonomia contínua

O engine consegue calcular drives, desejos, utility, hysteresis, silêncio e intenção. Também possui `schedule_idle()`. Porém:

```text
Core inicia
→ Scheduler existe
→ NÃO há loop normal Scheduler.run_due()
→ NÃO chama autonomy.schedule_idle()
```

Resultado: drives podem existir e crescer quando consultados, mas a Amora não acorda periodicamente só porque ficou ociosa.

### 7.2 Intenção → ação

Mesmo quando `AutonomyEngine.cycle()` escolhe uma intenção, ele publica `autonomy.decision`. Não foi encontrado subscriber normal para esse evento. `ActionEngine.plan_current()/execute_current()` existem em infraestrutura/API/CLI/testes, mas não são acionados pela decisão autônoma.

Resultado:

```text
cognição pode sugerir
→ BDI pode aceitar/rejeitar
→ intenção pode ser persistida
→ execução automática para aqui
```

Esse é um gap central para a promessa de “Amora pode iniciar/agir”.

### 7.3 ACIR/perspectiva

`ACIRCompiler` existe e é testado, mas o caminho Web normal é essencialmente:

```text
texto
→ LanguageSocialPipeline (addressee/social)
→ ConversationEngine
→ Cognition
```

Não foi encontrado passo normal equivalente a:

```text
PerspectiveResolver
→ ACIRCompiler
→ representação semântica canônica
→ provider
```

Logo parte da semântica que o canon diz pertencer ao Core ainda não está sendo consolidada no contrato normal.

### 7.4 Relações que evoluem

`RelationshipEvolutionService` existe, é instanciado e semeia relações canônicas. Entretanto não foi encontrado consumer normal que aplique aprendizagem relacional a partir da conversa/reflection. A flag `relationship_learning=false` de teste existe, mas sem um pipeline normal de aprendizagem ela hoje funciona mais como contrato futuro/metadado do que como um switch sobre uma mutação real.

### 7.5 PerspectiveConsolidator

Instanciado no Core e testado; nenhuma chamada normal encontrada. Classificação: **PRONTO NÃO INTEGRADO**.

### 7.6 Speaker tracking

`SessionSpeakerTracker` e `create_speaker_tracker()` existem, mas nenhum consumidor normal foi encontrado. A Web atual normalmente recebe speaker declarado (`PERSON_BK`) e não realiza diarização/identificação real.

## 8. O que realmente está sólido hoje

Os blocos abaixo têm integração real ponta a ponta e não precisam de reescrita preventiva:

- storage remoto/local + migrations;
- Event Bus;
- identidade/device/session/pairing;
- memória retrieval e reflection gated;
- World/Self;
- Emotion Engine no loop conversacional;
- DialoguePlan + RealizationFrame/validator;
- provider mesh e failover bounded;
- Conversation queue/ACK/cancel/barge-in;
- Web/PWA atual;
- STT;
- Fish PCM streaming + fallback explícito Gemini;
- Avatar Studio/Core e portable bundle;
- segurança/observabilidade/CI/container;
- backup operacional.

## 9. Prioridade recomendada antes de V1.1

### P0 — fazer o cérebro existente realmente viver

1. **Runtime Scheduler:** lifecycle explícito que chama `scheduler.run_due()` e agenda `autonomy.schedule_idle()`; cancellation/shutdown corretos.
2. **Autonomy → ActionEngine:** subscriber/bridge bounded de intenção aceita para planner/executor existente. Nunca pular risk/permission/effect verification.
3. **ACIR no caminho normal:** integrar PerspectiveResolver + ACIR antes do contrato cognitivo/provider sem duplicar DialoguePlan.
4. **Relationship learning real:** eventos determinísticos → RelationshipEvolutionService, com `relationship_learning` realmente controlando persistência e test isolation.
5. **Perspective consolidation:** integrar onde fatos de perspectiva viram claims, com provenance/confidence.

Esses itens devem vir antes de adicionar uma grande feature nova, porque já foram pagos em código e hoje entregam menos do que deveriam.

### P1 — limpeza e redução de confusão

1. remover `web/app.js`;
2. resolver ADR duplicado;
3. atualizar `PROJECT_STATE.md` e canon/roadmap de infraestrutura/voz;
4. exibir TTFS Fish no diagnóstico Web;
5. planejar achatamento da herança de cognição;
6. revisar Core `EnergyVad` e SpeakerTracker após definir plataforma que realmente os consome.

### P2 — presença visual / V1.1

Depois de P0:

1. integrar Avatar runtime à superfície principal/Living Console;
2. iniciar screen mapping/percepção visual;
3. OCR/keyframes/scene changes;
4. fechar corpo/presença final sem transformar o projeto num editor Live2D profissional.

### P3 — V1.2

Somente depois:

- preferência estética persistente;
- escolha/avaliação de roupa;
- desejo de mudar aparência;
- self-image ownership.

## 10. Ordem de limpeza segura

Não fazer uma “faxina” gigante. Ordem segura:

```text
mapear ownership
→ conectar módulos prontos
→ adicionar regressões
→ observar uso real
→ remover caminho morto comprovado
→ achatar duplicação evolutiva
→ só então reorganizar diretórios
```

A modularização alvo (`core/contracts/modules/providers/plugins/platforms/runtime`) continua válida, mas deve ser consequência de fronteiras reais. Mover centenas de arquivos antes de fechar os gaps de wiring apenas mudaria o endereço da dívida.

## 11. Decisão de projeto derivada desta auditoria

A próxima frente não deve ser “inventar mais cérebro”. Deve ser **integração do cérebro já construído**.

A ordem recomendada de desenvolvimento passa a ser:

```text
V1 wiring closure
  Scheduler/autonomia
  → intenção/ações
  → ACIR/perspectiva
  → relações/consolidação
  → limpeza de morto/duplicação

então

V1.1 percepção + presença visual
```

Isso maximiza reaproveitamento do trabalho já feito e reduz a sensação de módulos decorativos.
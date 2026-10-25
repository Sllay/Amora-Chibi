# AMORA — Version Lineage / Roadmap Canônico

**Status:** CANÔNICO  
**Atualizado em:** 2026-10-22

Este arquivo impede interpretar feature ausente da versão atual como “cancelada”. `MOVED` não significa cancelado.

---

## 1. Semântica de status

| Status | Significado |
|---|---|
| `IMPLEMENTED` | integrado ao produto/código; pode ainda exigir tuning |
| `IN_PROGRESS` | implementação/certificação ativa |
| `PLANNED` | aprovado para versão futura |
| `MOVED` | saiu de uma versão e ganhou destino explícito |
| `DEFERRED` | adiado sem destino fechado |
| `EXPERIMENTAL` | laboratório; não é canon operacional |
| `REJECTED` | método/feature rejeitado; consultar ADR |

---

## 2. Linhagem histórica

### V0.1 — Core Foundations

Event Bus, SQLite/migrations, schemas, API, TimeService/Scheduler, Entity Registry, Relationship Graph, Device/Session Identity, Secrets Vault e Model Router inicial.

Destino atual: fundação absorvida pelo Core V1.

### V0.2 — Language / Social Foundation

VAD/STT, speaker/addressee, Social Scene, Perspective Resolver e ACIR.

Destino atual: fundação social/cognitiva V1.

### V0.3 — Memória

Raw Event Log, memória episódica/semântica/relacional, provenance, retrieval e backup inicial.

Destino atual: Memory System V1.

### V0.4 — Identidade avançada / World Model

Relationship evolution, self model, beliefs, world model e consolidação de perspectiva.

Destino atual: estado canônico V1.

### V0.5 — Emoção

Appraisal determinístico, Emotion Events causais, Global Mood, Entity Affect, blame/intentionality/spillover, decay, reparação/ambivalência e Social Bids candidatos.

Destino atual: Emotion Engine V1. A integração normal conversa → appraisal → Emotion Engine foi fechada no Step 2.

### V0.6 — Autonomia

Drives, Utility, BDI, hysteresis e arbitragem de Social Bids.

Destino atual: Autonomy Engine V1.

### V0.7 — Ações

Plugins, tools, planner, skills e execution monitor.

Destino atual: action/plugin infrastructure V1.

### V0.8 — Conversa natural

Voice pipeline, Turn Manager, backchannels, autonomia social e conversa/cognição inicial.

Destino atual: Conversation + Cognition V1.

---

## 3. V0.9/V0.95 antigos

O planejamento antigo misturava corpo/percepção, estética persistente e fechamento cognitivo/voz. Foi dividido, não descartado.

```text
PLANEJAMENTO ANTIGO V0.9 / V0.95
│
├── renderer / corpo / avatar final ─────────────→ V1.1
├── percepção visual / screen mapping ───────────→ V1.1
├── reação visual integrada ao corpo ────────────→ V1.1
│
├── gosto estético persistente ──────────────────→ V1.2
├── escolha / avaliação de roupa ────────────────→ V1.2
├── desejo de alterar aparência ─────────────────→ V1.2
└── ownership de autoimagem ─────────────────────→ V1.2
```

Infraestrutura/experimentos antigos de avatar podem permanecer no repo, mas não bloqueiam o fechamento V1.0.

---

## 4. V1.0 — fechamento atual

**Status:** `IN_PROGRESS — VOICE IDENTITY LAB-APPROVED / PHYSICAL CERTIFICATION PENDING`

Fundação atual inclui:

- Context Compiler;
- Conversation History literal;
- Cognitive Journal;
- barge-in/cancellation;
- structured proposals e structured surface realization atrás de gates locais;
- autonomia;
- routing/quota/backoff/failover;
- reflection;
- Cognitive Replay;
- privacy/consent remoto;
- backup/restore/observabilidade;
- Dialogue Orchestrator local;
- Emotion Engine em loop fechado com conversa;
- test-state isolation;
- provider mesh zero-cost fail-closed;
- Android app/pareamento/foreground voice;
- voice path sherpa-onnx fisicamente comprovado no Galaxy A15;
- Safari/Web como superfície primária de iteração rápida, conforme ADR-0013;
- Voice Worker local Web com identidade vocal aprovada de laboratório, conforme ADR-0014.

### V1 Step 1 — Local Voice

**Status:** `IMPLEMENTED / PHYSICAL PATH PASS / MERGED`

Entregue:

- VoiceIdentity pt-BR provisória;
- sherpa-onnx + Piper/VITS Faber;
- voice pack verificado/empacotado em build-time assets;
- síntese offline;
- Gemini TTS fora do caminho normal;
- sem fallback silencioso Google/Android;
- diagnóstico local;
- Galaxy A15 reproduziu fisicamente Faber.

Faber continua voz técnica provisória no caminho Android de produção existente. Long-run barge-in/térmica/conversa permanecem no gate final V1.

### V1 Step 2 — Dialogue Identity + Emotion Integration

**Status:** `IMPLEMENTED / MERGED`

Entregue:

- atos locais ricos;
- stance e relational attitude;
- closing mode;
- RealizationFrame estruturado;
- validator local antes da fala;
- realização local para atos triviais/self-facts;
- conversation events alimentando Emotion Engine antes do DialoguePlan;
- emoção influenciando comportamento antes da prosódia;
- test-mode sem consolidar estado persistente;
- observabilidade de appraisal/plan/realization;
- addressee continuity sem auto-renovação;
- cognitive mode aplicado antes do plano.

PR #29 / merge `dfe50de9a9312f0e6a3f08cc482e9b4dc0aee1d4`.

CI: Python 3.11–3.14 PASS, 211 pytest tests por matriz e smokes de doctor/autonomy/actions/conversation/avatar/backup PASS naquele checkpoint.

### V1 Step 3 — Zero-Cost Provider Mesh

**Status:** `IMPLEMENTED / MERGED`

Entregue:

- `docs/canon/PROVIDER_MESH.md`;
- Groq primary;
- Cloudflare Workers AI como segundo eixo direto;
- Gemini fallback independente para cognição compatível, não para identidade vocal normal;
- OpenRouter free-route como specialist/last resort;
- fallback local;
- quota/cooldown/circuit breaker/failover preservados;
- zero-cost guards fail-closed;
- health/usage observability;
- deterministic offline provider-mesh tests.

Guardas:

- Cloudflare só registra com configuração + confirmação explícita de zero-cost;
- OpenRouter só fica disponível para rota explicitamente gratuita;
- nenhuma ausência de provider quebra o Core;
- nenhum adapter ativa billing/pago silenciosamente.

PR #30 foi integrado ao `main`; o provider mesh faz parte da fundação V1 atual.

### V1 Step 4 — Canonical Voice + V1 Certification

**Status:** `IN_PROGRESS / FINAL_B + SIGNATURE V1 SOFT LAB-APPROVED / PHYSICAL GATE PENDING`

Decisões vigentes:

- ADR-0009: backend pronto + VoiceSignature substitui treino próprio como requisito V1;
- ADR-0010: `AMORA_CHILD_FINAL_B_PLUS60` congelada como base aprovada de laboratório;
- ADR-0011: `AMORA_VOICE_SIGNATURE_V1_SOFT` aceita como assinatura aprovada de laboratório;
- ADR-0014: a mesma implementação FINAL_B + Signature é compartilhada entre Android JNI e o worker Termux/Web.

Pipeline atual da candidata:

```text
Supertonic 3 INT8
↓
style 60% speaker 0 + 40% speaker 4
↓
AMORA_CHILD_FINAL_B_PLUS60
↓
AMORA_VOICE_SIGNATURE_V1_SOFT
↓
Emotion / Prosody V2 bounded
↓
PCM
```

A busca por idade/tamanho vocal base e a assinatura V1 Soft não devem ser reabertas sem evidência nova. Emoção/prosódia varia ao redor da identidade fixa, sem trocar timbre.

Entregue nesta etapa:

- Voice Lab Android isolado;
- Supertonic 3 INT8 via sherpa-onnx;
- `amora_voice.bin` com style 60/40;
- FINAL_B congelada;
- VoiceSignature V1 Soft congelada;
- Emotion/Prosody V2 bounded;
- processador C++ compartilhado entre JNI Android e Termux;
- Voice Worker local Web/Safari emitindo WAV PCM16;
- Web Audio no Safari sem `speechSynthesis` como transporte principal;
- entrega baseada em claim → render → started → playback real → ACK;
- cancelamento de barge-in/manual stop;
- falha de voz fail-open para texto/interface;
- `WEB_SAFARI` separado de `DEVICE_A15`.

Gates restantes:

1. preparar o worker no Termux real;
2. reproduzir a candidata pelo Safari/iPhone;
3. medir primeira latência e RTF reais;
4. validar barge-in/manual stop físicos;
5. observar RAM/CPU/temperatura e estabilidade prolongada;
6. confirmar inteligibilidade/qualidade acústica no caminho Web;
7. conversa longa, provider failover, restart e backup/restore;
8. fechar certificação/documentação V1.

CI não substitui esses gates físicos.

---

## 5. Política de experimentos V1

Um experimento/teste novo deve existir apenas quando seu resultado:

1. decide entre arquiteturas reais;
2. prova gate físico/hardware;
3. detecta/previne regressão crítica;
4. valida contrato destinado à produção.

Evitar experimentos descartáveis que apenas demonstram algo já provado.

---

## 6. V1.0.1 — Web/PWA + portabilidade de plataforma

**Status:** `IN_PROGRESS — SAFARI PRIMARY ITERATION SURFACE / LOCAL VOICE WORKER MERGED`

ADR-0013 antecipou esta frente antes do fechamento físico completo da voz Android porque o ciclo APK → instalar → testar estava dominando o tempo de iteração de um cliente debug descartável.

Objetivo atual: usar o Safari como laboratório conversacional principal sem mover identidade/estado canônicos para o navegador.

Implementado:

- cliente Web/PWA servido sob `/web/`;
- pareamento de 6 dígitos e autenticação reutilizando o Core;
- chat por texto;
- microfone por Web Audio;
- WAV → STT pelo contrato já existente;
- VAD e barge-in no navegador;
- fila de até múltiplos segmentos;
- diagnóstico de latência/fila/voz;
- `test_mode=true` por padrão no laboratório;
- Quick Tunnel HTTPS para desenvolvimento;
- routing explícito `WEB_SAFARI`;
- Voice Worker local Termux com Supertonic 60/40 + FINAL_B + Signature V1 Soft;
- WAV PCM16 para Safari via `AudioContext`;
- texto independente da disponibilidade do áudio;
- ACK apenas depois de playback real;
- cancelamento de fetch/playback/Core em interrupção;
- ausência de fallback silencioso para Safari TTS ou Gemini TTS.

Merge principal da voz Web: PR #54 / `5ea4bd7d40e85cf7380f50f0aad05b54605f0ade`.

Próximo gate desta frente:

- setup único do worker no Termux;
- teste físico no Safari/iPhone;
- confirmar voz, latência, fila e barge-in reais;
- depois disso o ciclo de laboratório deve ser majoritariamente `git pull → reiniciar Core → atualizar Safari → testar`, sem novo APK para mudanças que não sejam Android-specific.

### Core remoto / disponibilidade contínua — ADR-0015

Origem: V1.0.1 portabilidade; destino: V1.0.1 migração incremental;
status: IN_PROGRESS; motivo: decisão explícita de BK em 2026-10-23.

Core/Web em Linux (Koyeb candidato), SQLite local ou libSQL remoto (Turso
candidato), mesma voz em worker independente (Modal candidato). A15 deixa
host principal e será Living Console. Não altera V1.1/V1.2 nem cancela SQLite.
Auditoria, fronteiras, container, storage remoto, voz remota e observabilidade
são etapas separadas. Deploy e certificação reais dependem de gates de
persistência/backup/restore, privacidade, custo, latência e áudio físico.

---

## 7. V1.1 — percepção e corpo

**Status:** `PLANNED`

- percepção visual;
- screen mapping;
- keyframes/OCR/scene changes;
- corpo/avatar final;
- renderer/rig/expressões necessários;
- integração Mind → Avatar;
- presença visual mobile/stream quando aplicável.

Não transformar V1.1 automaticamente em editor profissional de Live2D/rig completo.

---

## 8. V1.2 — estética e autoimagem

**Status:** `PLANNED`

- preferências estéticas persistentes;
- desejo de alterar aparência;
- escolha/avaliação de roupa;
- relação com a própria imagem;
- self-image ownership;
- evolução estética coerente com identidade/memória.

Essas capacidades devem ser da Amora, não presets controlados pelo provider.

---

## 9. Modularização estrutural — direção contínua

**Status:** `PLANNED / INCREMENTAL`

A evolução deve reduzir acoplamento a scripts grandes e preservar um Core pequeno/estável.

Taxonomia alvo:

```text
core/       -> identidade, estado, decisão e orquestração
contracts/  -> eventos, schemas e capabilities
modules/    -> voz, audição, visão, memória, presença
providers/  -> cognição, STT, TTS e implementações substituíveis
plugins/    -> integrações externas (Discord, GitHub, etc.)
platforms/  -> Android, Web e futuros adapters
runtime/    -> registry, loader, event bus e lifecycle
```

Regra: adicionar Discord, trocar TTS ou criar cliente Web não deve exigir cirurgia no Cognitive Core.

Essa modularização será feita por fronteiras reais/contratos e refactors guiados por uso, não por reescrita total preventiva.

---

## 10. Futuro / backlog

Possibilidades sem compromisso de versão:

- plugins de games;
- Minecraft;
- navegador;
- Discord;
- OBS/PRISM avançado;
- automações externas;
- modelos RP especializados;
- TTS local zero-shot/voice cloning alternativo;
- modelo de voz treinado próprio caso volte a ter vantagem clara;
- outros backends após benchmark necessário;
- ferramentas especializadas via OpenRouter;
- singing synthesis separado do TTS normal, compartilhando identidade vocal quando viável.

Nada desta seção entra automaticamente na V1/V1.1/V1.2.

---

## 11. Regra para mudanças futuras

Quando uma decisão de versão mudar:

1. não apagar silenciosamente o item antigo;
2. registrar origem, novo destino e motivo;
3. criar/atualizar ADR quando relevante;
4. atualizar canon/roadmap;
5. manter histórico Git.

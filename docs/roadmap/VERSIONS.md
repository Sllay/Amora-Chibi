# AMORA — Version Lineage / Roadmap Canônico

**Status:** CANÔNICO  
**Atualizado em:** 2026-11-03

Este arquivo registra **linhagem, destino e ordem de grandes frentes**. Detalhe operacional atual fica em `PROJECT_STATE.md`. `MOVED` não significa cancelado.

---

## 1. Semântica de status

| Status | Significado |
|---|---|
| `IMPLEMENTED` | integrado ao código/produto; pode ainda exigir tuning |
| `IN_PROGRESS` | implementação/certificação ativa |
| `PLANNED` | aprovado para versão futura |
| `MOVED` | saiu de uma versão e ganhou destino explícito |
| `DEFERRED` | adiado sem destino fechado |
| `EXPERIMENTAL` | laboratório; não é canon operacional |
| `REJECTED` | método/feature rejeitado; consultar ADR |

---

## 2. Linhagem histórica V0.x

### V0.1 — Core Foundations

Event Bus, SQLite/migrations, schemas, API, TimeService/Scheduler, Entity Registry, Relationship Graph, Device/Session Identity, Secrets Vault e Model Router inicial.

**Destino:** fundação absorvida pelo Core V1.

### V0.2 — Language / Social Foundation

VAD/STT, speaker/addressee, Social Scene, Perspective Resolver e ACIR.

**Destino:** fundação social/cognitiva V1.

### V0.3 — Memória

Raw Event Log, memória episódica/semântica/relacional, provenance, retrieval e backup inicial.

**Destino:** Memory System V1.

### V0.4 — Identidade avançada / World Model

Relationship evolution, self model, beliefs, world model e consolidação de perspectiva.

**Destino:** estado canônico V1.

### V0.5 — Emoção

Appraisal determinístico, Emotion Events, Global Mood, Entity Affect, blame/intentionality/spillover, decay, reparação/ambivalência e Social Bids.

**Destino:** Emotion Engine V1.

### V0.6 — Autonomia

Drives, Utility, BDI, hysteresis e arbitragem de Social Bids.

**Destino:** Autonomy Engine V1.

### V0.7 — Ações

Plugins, tools, planner, skills e execution monitor.

**Destino:** action/plugin infrastructure V1.

### V0.8 — Conversa natural

Voice pipeline, Turn Manager, backchannels, autonomia social e cognição/conversa inicial.

**Destino:** Conversation + Cognition V1.

---

## 3. V0.9 / V0.95 antigos — itens movidos

O planejamento antigo misturava corpo/percepção, estética persistente e fechamento cognitivo/voz. Foi dividido, não descartado.

```text
PLANEJAMENTO ANTIGO V0.9 / V0.95
│
├── renderer / corpo / avatar final ─────────────→ V1.1
├── continuous screen/environment vision ────────→ V1.1
├── reação visual integrada ao corpo ────────────→ V1.1
│
├── gosto estético persistente ──────────────────→ V1.2
├── escolha / avaliação de roupa ────────────────→ V1.2
├── desejo de alterar aparência ─────────────────→ V1.2
└── ownership de autoimagem ─────────────────────→ V1.2
```

**Importante:** Perceptual Asset Vision de sticker/emoji/imagem pontual não é a antiga screen vision. Foi antecipada para CH2 da linha atual.

---

## 4. V1.0 — fechamento cognitivo, social e vocal

**Status:** `IN_PROGRESS — BASE COGNITIVA/SOCIAL CONSOLIDADA / PHYSICAL VOICE + LIVE PROVIDER GATES PENDING`

Fundação vigente inclui:

- Memory / World / Self / Relationship;
- Emotion Engine e autonomia;
- DialoguePlan + RealizationFrame + validator local;
- Context Compiler / Conversation History / Cognitive Journal;
- provider mesh zero-cost fail-closed;
- recovery-before-fallback + circuit lifecycle por provider/task;
- capabilities explícitas + authority/policy local;
- response modality competition;
- Platform Messaging + Social Attention;
- persistent perceptual expression memory;
- Perceptual Asset Vision pontual do CH2;
- provider certification harness + identical-context A/B do CH3;
- backup/restore/observabilidade/security;
- Android/Web voice paths e identidade vocal de laboratório.

### V1 Step 1 — Local Voice

**Status:** `IMPLEMENTED / PHYSICAL PATH PASS / MERGED`

Sherpa-onnx/Piper-Faber estabeleceu o primeiro path local comprovado no Galaxy A15. Faber permanece voz técnica provisória do caminho Android legado.

### V1 Step 2 — Dialogue Identity + Emotion Integration

**Status:** `IMPLEMENTED / MERGED`

Atos locais ricos, stance/relational attitude, closing mode, RealizationFrame, validator local, emotion → DialoguePlan e observabilidade foram integrados.

Fase 3/perspectiva: anotação de referências em texto livre, grafo com disclosure,
ACIR/plano/payload e sessão Web de teste integrados (ADR-0040). Estado:
TECHNICALLY IMPLEMENTED; validação Mistral remota pendente. Escopo permanece V1.0.

PR #29 / merge `dfe50de9a9312f0e6a3f08cc482e9b4dc0aee1d4`.

### V1 Step 3 — Zero-Cost Provider Mesh

**Status:** `IMPLEMENTED / MERGED / CANON UPDATED`

Provider mesh, quota/cooldown/circuit state, health/usage e zero-cost guards existem. ADR-0033 e `PROVIDER_MESH.md` congelam **recovery-before-fallback** como direção obrigatória; CH3 integrou a infraestrutura de certificação e o lifecycle de resiliência, mas a decisão live entre candidatos continua separada.

### V1 Step 4 — Canonical Voice + V1 Certification

**Status:** `IN_PROGRESS / LAB-APPROVED / PHYSICAL GATES PENDING`

Decisões de voz vigentes continuam:

- ADR-0009: backend pronto + VoiceSignature substitui treino próprio como requisito V1;
- ADR-0010: `AMORA_CHILD_FINAL_B_PLUS60` congelada como base de laboratório;
- ADR-0011: `AMORA_VOICE_SIGNATURE_V1_SOFT` aprovada em laboratório;
- ADR-0014: mesma implementação compartilhada entre Android JNI e worker Web/Termux.

Gates físicos continuam separados de CI: Safari/iPhone, latência/RTF, barge-in/manual stop, RAM/CPU/temperatura, conversa longa e estabilidade real.

---

## 5. Linha atual de engenharia — somente 3 CHs

Para evitar novo acúmulo de pontas soltas, a linha ativa continua reduzida a três movimentos grandes.

### CH1 — Consolidação e continuidade

**Status:** `COMPLETED`

Promovido e recertificado:

- #116 capability awareness/perceptual memory canon;
- #117 canonical capability registry;
- #118 cognitive capability projection;
- #119 capability-grounded actions;
- #120 response modality competition;
- #121 persistent perceptual expression memory / schema 16;
- #122 checkpoint/canon/roadmap/ADR de consolidação.

#115 foi fechada sem merge por estar supersedida.

Documentação/decisão: `PROJECT_STATE.md`, `PROVIDER_MESH.md`, `PERCEPTUAL_ASSET_MEMORY.md` e ADR-0033.

### CH2 — Visual Expression Enrichment

**Status:** `COMPLETED / MERGED — PR #123 / ac13f4d5`

Merge em `main`: `ac13f4d5a84dff2a908c47d28ccf4cf4a4c539af`.

Objetivo entregue: transformar `pending_visual` em CPF realmente percebido por conteúdo.

```text
relevant expression asset
→ claim/lease
→ secure Discord resolver/fetch
→ real-byte SHA-256
→ [reuse known SHA | multimodal provider]
→ strict structured semantic profile
→ local validation
→ metadata+content+claim CAS
→ ENRICHED
```

Entregue:

- schema 17;
- metadata fingerprint separado do content SHA;
- claim/lease/idempotência e stale-result rejection;
- authenticated worker boundary;
- secure Discord resolver + bounded fetch;
- Core recomputando SHA dos bytes reais;
- privacy/remote-processing gate persistente;
- `VisionAnalyzer` substituível;
- `GroqQwenVisionAnalyzer` com `qwen/qwen3.8-27b`;
- strict JSON Schema no endpoint + validação Pydantic local;
- provider/model provenance;
- same-SHA reuse sem nova chamada e sem nova semantic revision;
- retry/defer assíncrono;
- gate perceptual CI cobrindo contratos CH2.

Formatos certificados diretamente: PNG/JPEG/WebP estáticos.

GIF/Lottie/animação sem renderer determinístico ficam **fail-closed/deferred**. Não inferir significado completo por primeiro frame arbitrário.

Head de código certificado: `7ea117b512f1e10ad029897e1172c3f3902b1862`.

Gates verdes nesse head:

- Core tests;
- Perceptual expression memory;
- Messaging workers;
- Response modality competition;
- Cloud container contracts;
- Pre-deploy security.

Gates live ainda abertos e explicitamente separados:

- chamada real Groq/Qwen com credencial operacional;
- sticker real Discord atravessando resolver/worker/Core/provider;
- reconnect/intents/permissões do Discord em ambiente real.

Esses gates permanecem operacionais e não reabrem o CH2 software.

**Fora do CH2:** screen capture, vídeo contínuo, OCR temporal, avatar/corpo.

### CH3 — Provider Certification + Resilience

**Status:** `IN_PROGRESS — SOFTWARE MERGED / LIVE CERTIFICATION PENDING — PR #124 / b72e7d1e`

Software integrado em `main` via PR #124.

- head final certificado: `769cba5eae1677a5867a3765aab12c077abcf6fb`;
- merge: `b72e7d1e7628ff58e02042b3658863b481c02bfe`;
- schema permanece 17.

Integrado:

- `SOCIAL_SURFACE_REPLAY_V1`;
- Mistral Large 4 como candidato `certification_only`;
- A/B identical-context Qwen × Mistral;
- context fingerprint e execução sequencial;
- mechanical score sem autoridade de promoção;
- Retry-After delay-seconds + HTTP-date;
- bounded backoff + jitter;
- long Retry-After fora do budget interativo → circuit/cooldown/failover sem retry inline;
- structured repair bounded no runtime estruturado;
- proxy de resiliência direto preservado como transport-only por padrão;
- código semântico de realization preservado separadamente da classificação `PROVIDER_STRUCTURED_OUTPUT_INVALID`;
- circuit registry por provider + task;
- `CLOSED → OPEN → HALF_OPEN` com probe único;
- ProviderRouter continua dono do ranking/quota.

No fechamento da #124, a recertificação contra `main` revelou três regressões de contrato — expectativa legada de Retry-After longo, mistura de validação semântica no proxy de transporte e perda do código semântico na observabilidade. As três foram corrigidas antes do merge; o head final passou:

- Core tests em Python 3.11 / 3.12 / 3.13 / 3.14;
- Provider certification;
- Cloud container contracts;
- Pre-deploy security;
- Cognitive capability projection;
- Capability grounded intent;
- Response modality competition.

Próxima sequência do mesmo CH3:

```text
live Qwen × Mistral social replay
→ human review
→ latency/quota/cost evidence
→ Mistral × GPT-OSS reasoning replay
→ promote only where evidence wins
```

Baseline permanece:

```text
SOCIAL DIALOGUE: Qwen 3.8 27B
REASONING: GPT-OSS 120B
MISTRAL LARGE 4: candidate only
```

**O merge da #124 promoveu a infraestrutura CH3, não o Mistral.** Nenhum modelo vira identidade da Amora.

---

## 6. V1.0.1 — Web/PWA + portabilidade

**Status:** `IN_PROGRESS — SAFARI PRIMARY ITERATION SURFACE / LOCAL VOICE WORKER MERGED`

Permanece válida a direção do ADR-0013/ADR-0015:

- Web/PWA como superfície rápida;
- Core/identidade fora do browser;
- `WEB_SAFARI != DEVICE_A15`;
- Core/Web podem operar em Linux remoto;
- SQLite local ou libSQL remoto continuam opções substituíveis;
- voice worker pode ser independente;
- disponibilidade/custo/privacidade/backup continuam gates reais.

Platform Messaging/People/Console e workers Discord/WhatsApp já possuem fundação em código; staging físico continua separado de “implementado”.

---

## 7. V1.1 — Continuous Environment Vision + corpo

**Status:** `PLANNED`

V1.1 significa explicitamente **percepção contínua do ambiente/tela**, não análise pontual de sticker.

```text
screen / video / environment
→ periodic capture
→ scene-change detection
→ keyframes
→ OCR / objects / visual concepts
→ temporal context
→ audio/event synchronization
→ world/perception events
```

Também permanecem em V1.1:

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

Essas capacidades pertencem à Amora/Core, não ao provider.

---

## 9. Modularização estrutural — direção contínua

**Status:** `PLANNED / INCREMENTAL`

Taxonomia alvo continua:

```text
core/       -> identidade, estado, decisão e orquestração
contracts/  -> eventos, schemas e capabilities
modules/    -> voz, audição, visão, memória, presença
providers/  -> cognição, STT, TTS e implementações substituíveis
plugins/    -> integrações externas
platforms/  -> Android, Web e futuros adapters
runtime/    -> registry, loader, event bus e lifecycle
```

Refactor segue fronteiras reais e replay/testes, não reescrita preventiva total.

---

## 10. Futuro / backlog

Sem compromisso automático de versão:

- plugins de games/Minecraft;
- navegador/OBS/PRISM avançado;
- automações externas;
- modelos RP especializados;
- TTS/voice cloning alternativos;
- modelo de voz próprio se voltar a ter vantagem clara;
- outros backends após benchmark;
- singing synthesis separado do TTS normal.

---

## 11. Regra para mudanças futuras

Quando uma decisão mudar:

1. não apagar silenciosamente a origem;
2. registrar destino e motivo;
3. atualizar ADR/canon/roadmap quando relevante;
4. atualizar `PROJECT_STATE.md` no mesmo CH;
5. manter histórico Git;
6. não abrir uma nova frente grande com o CH anterior documentalmente quebrado.

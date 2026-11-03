# AMORA — Version Lineage / Roadmap Canônico

**Status:** CANÔNICO  
**Atualizado em:** 2026-11-02

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

**Importante:** Perceptual Asset Vision de sticker/emoji/GIF não é a antiga screen vision e foi antecipada para CH2 da linha atual.

---

## 4. V1.0 — fechamento cognitivo, social e vocal

**Status:** `IN_PROGRESS — BASE COGNITIVA/SOCIAL CONSOLIDADA / PHYSICAL VOICE GATES PENDING`

Fundação vigente inclui:

- Memory / World / Self / Relationship;
- Emotion Engine e autonomia;
- DialoguePlan + RealizationFrame + validator local;
- Context Compiler / Conversation History / Cognitive Journal;
- provider mesh zero-cost fail-closed;
- capabilities explícitas + authority/policy local;
- response modality competition;
- Platform Messaging + Social Attention;
- persistent perceptual expression memory;
- backup/restore/observabilidade/security;
- Android/Web voice paths e identidade vocal de laboratório.

### V1 Step 1 — Local Voice

**Status:** `IMPLEMENTED / PHYSICAL PATH PASS / MERGED`

Sherpa-onnx/Piper-Faber estabeleceu o primeiro path local comprovado no Galaxy A15. Faber permanece voz técnica provisória do caminho Android legado.

### V1 Step 2 — Dialogue Identity + Emotion Integration

**Status:** `IMPLEMENTED / MERGED`

Atos locais ricos, stance/relational attitude, closing mode, RealizationFrame, validator local, emotion → DialoguePlan e observabilidade foram integrados.

PR #29 / merge `dfe50de9a9312f0e6a3f08cc482e9b4dc0aee1d4`.

### V1 Step 3 — Zero-Cost Provider Mesh

**Status:** `IMPLEMENTED / MERGED / CANON UPDATED`

Provider mesh, quota/cooldown/circuit state, health/usage e zero-cost guards existem. ADR-0033 e `PROVIDER_MESH.md` agora congelam **recovery-before-fallback** como direção obrigatória; implementação/certificação completa pertence ao CH3.

### V1 Step 4 — Canonical Voice + V1 Certification

**Status:** `IN_PROGRESS / LAB-APPROVED / PHYSICAL GATES PENDING`

Decisões de voz vigentes continuam:

- ADR-0009: backend pronto + VoiceSignature substitui treino próprio como requisito V1;
- ADR-0010: `AMORA_CHILD_FINAL_B_PLUS60` congelada como base de laboratório;
- ADR-0011: `AMORA_VOICE_SIGNATURE_V1_SOFT` aprovada em laboratório;
- ADR-0014: mesma implementação compartilhada entre Android JNI e worker Web/Termux.

Pipeline congelado:

```text
Supertonic 3 INT8
→ style 60% speaker 0 + 40% speaker 4
→ AMORA_CHILD_FINAL_B_PLUS60
→ AMORA_VOICE_SIGNATURE_V1_SOFT
→ Emotion / Prosody V2 bounded
→ PCM
```

Gates físicos ainda não podem ser marcados PASS por CI: Safari/iPhone, latência/RTF, barge-in/manual stop, RAM/CPU/temperatura, conversa longa e estabilidade real.

---

## 5. Linha atual de engenharia — somente 3 CHs

Para evitar novo acúmulo de pontas soltas, a linha ativa fica reduzida a três movimentos grandes.

### CH1 — Consolidação e continuidade

**Status:** `COMPLETED`

Promovido e recertificado:

- #116 capability awareness/perceptual memory canon;
- #117 canonical capability registry;
- #118 cognitive capability projection;
- #119 capability-grounded actions;
- #120 response modality competition;
- #121 persistent perceptual expression memory / schema 16.

#115 foi fechada sem merge por estar supersedida.

Também foram corrigidos testes antigos que hardcodavam schema 15; agora dependem de `LATEST_SCHEMA_VERSION`.

Documentação/decisão: `PROJECT_STATE.md`, `PROVIDER_MESH.md`, `PERCEPTUAL_ASSET_MEMORY.md` e ADR-0033.

### CH2 — Visual Expression Enrichment

**Status:** `NEXT`

Objetivo único: transformar `pending_visual` em CPF realmente percebido.

```text
relevant expression asset
→ secure resolver/fetch
→ real-byte SHA-256
→ multimodal provider
→ strict structured semantic profile
→ local validation
→ apply_visual_enrichment(expected_fingerprint)
→ ENRICHED
```

Inclui:

- SSRF/redirect/MIME/size/timeouts;
- PNG/JPEG/WebP e política bounded para animated formats;
- claim/lease/idempotência;
- stale-result rejection;
- privacidade/provenance;
- retry/defer assíncrono;
- nenhum reprocessamento do mesmo fingerprint sem motivo.

**Fora do CH2:** screen capture, vídeo contínuo, OCR temporal, avatar/corpo.

### CH3 — Provider Certification + Resilience

**Status:** `AFTER CH2`

Certificar por task class, sem “modelo central = identidade”:

```text
SOCIAL DIALOGUE
Qwen 3.8 27B baseline
vs
Mistral Large 4 candidate

REASONING
GPT-OSS 120B baseline
vs
Mistral Large 4 candidate

PERCEPTION
Mistral Large 4 candidate
+ fallback somente se benchmark/política justificar
```

Replay deve medir pt-BR, personality fit, DialoguePlan fidelity, grounding, humor/palavrão contextual, conversa adulta permitida, assistantism, moralização/recusa social indevida, structured output, latência, disponibilidade e custo.

Resiliência obrigatória:

```text
classify failure
→ bounded recovery do primary
→ backoff + jitter / Retry-After
→ circuit breaker + HALF_OPEN
→ fallback OR defer
```

Se Mistral vencer diálogo, Qwen vira fallback. Se vencer reasoning, GPT-OSS vira fallback. Promoção só depois de evidência reproduzível.

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

V1.1 agora significa explicitamente **percepção contínua do ambiente/tela**, não análise pontual de sticker.

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

Refactor deve seguir fronteiras reais e replay/testes, não reescrita preventiva total.

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

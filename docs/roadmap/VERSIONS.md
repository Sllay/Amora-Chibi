# AMORA — Version Lineage / Roadmap Canônico

**Status:** CANÔNICO  
**Atualizado em:** 2026-10-11

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

**Status:** `IN_PROGRESS — STEP 4 NEXT`

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
- voice path sherpa-onnx fisicamente comprovado no Galaxy A15.

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

Faber continua voz técnica provisória. Long-run barge-in/térmica/conversa permanecem no gate final V1.

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

CI: Python 3.11–3.14 PASS, 211 pytest tests por matriz e smokes de doctor/autonomy/actions/conversation/avatar/backup PASS.

### V1 Step 3 — Zero-Cost Provider Mesh

**Status:** `IMPLEMENTED / CI PASS / READY TO MERGE`

Entregue:

- `docs/canon/PROVIDER_MESH.md`;
- Groq primary;
- Cloudflare Workers AI como segundo eixo direto;
- Gemini fallback independente;
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

PR #30. CI run `38172639477`: Python 3.11–3.14 PASS, pytest e todos os smoke tests PASS sem chamadas reais aos providers.

### V1 Step 4 — Canonical Voice + V1 Certification

**Status:** `NEXT / VOICE TARGET CANONIZED`

Objetivo:

- seguir `docs/canon/VOICE_IDENTITY.md`;
- selecionar material feminino pt-BR com direitos/provenance claros;
- priorizar GPU gratuita para treino/fine-tuning;
- medium/high escolhido por naturalidade + benchmark, não pelo rótulo;
- exportar ONNX e manter inferência local no sherpa-onnx;
- integrar Prosody Planner bounded;
- teste prolongado no A15;
- conversa, barge-in, provider failover, restart e backup/restore;
- estabilidade térmica/memória;
- fechamento documental V1.

Compute pago não é dependência. Se for avaliado no futuro, exige teto explícito de custo e decisão consciente. Treino no Galaxy A15 não é padrão: RAM do aparelho não equivale a VRAM de GPU.

---

## 5. Política de experimentos V1

Um experimento/teste novo deve existir apenas quando seu resultado:

1. decide entre arquiteturas reais;
2. prova gate físico/hardware;
3. detecta/previne regressão crítica;
4. valida contrato destinado à produção.

Evitar experimentos descartáveis que apenas demonstram algo já provado.

---

## 6. V1.1 — percepção e corpo

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

## 7. V1.2 — estética e autoimagem

**Status:** `PLANNED`

- preferências estéticas persistentes;
- desejo de alterar aparência;
- escolha/avaliação de roupa;
- relação com a própria imagem;
- self-image ownership;
- evolução estética coerente com identidade/memória.

Essas capacidades devem ser da Amora, não presets controlados pelo provider.

---

## 8. Futuro / backlog

Possibilidades sem compromisso de versão:

- plugins de games;
- Minecraft;
- navegador;
- Discord;
- OBS/PRISM avançado;
- automações externas;
- modelos RP especializados;
- TTS local zero-shot/voice cloning alternativo;
- outros backends após benchmark necessário;
- ferramentas especializadas via OpenRouter.

Nada desta seção entra automaticamente na V1/V1.1/V1.2.

---

## 9. Regra para mudanças futuras

Quando uma decisão de versão mudar:

1. não apagar silenciosamente o item antigo;
2. registrar origem, novo destino e motivo;
3. criar/atualizar ADR quando relevante;
4. atualizar canon/roadmap;
5. manter histórico Git.

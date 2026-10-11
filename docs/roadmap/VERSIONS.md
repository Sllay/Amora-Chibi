# AMORA — Version Lineage / Roadmap Canônico

**Status:** CANÔNICO  
**Atualizado em:** 2026-10-10

Este arquivo existe para impedir um erro recorrente: interpretar uma feature ausente da versão atual como “cancelada”.

Cada item importante deve indicar origem, destino atual e motivo de movimento quando aplicável.

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

`MOVED` **não** significa cancelado.

---

## 2. Linhagem histórica

### V0.1 — Core Foundations

Escopo histórico:

- Event Bus;
- SQLite/migrations;
- schemas;
- API;
- TimeService/Scheduler;
- Entity Registry;
- Relationship Graph;
- Device/Session Identity;
- Secrets Vault;
- Model Router inicial.

Destino atual: fundação absorvida pelo Core V1.

### V0.2 — Language / Social Foundation

Escopo histórico:

- VAD/STT;
- speaker/addressee;
- Social Scene;
- Perspective Resolver;
- ACIR.

Destino atual: fundação social/cognitiva do Core V1.

### V0.3 — Memória

Escopo histórico:

- Raw Event Log;
- memória episódica/semântica/relacional;
- provenance;
- retrieval;
- backup inicial.

Destino atual: Memory System V1.

### V0.4 — Identidade avançada / World Model

Escopo histórico:

- relationship evolution;
- self model;
- beliefs;
- world model;
- consolidação de perspectiva.

Destino atual: estado canônico V1.

### V0.5 — Emoção

Escopo histórico concluído em 2026-10-08:

- appraisal determinístico;
- Emotion Events causais;
- Global Mood;
- Entity Affect;
- blame / intentionality / spillover;
- decay;
- reparação/ambivalência;
- Social Bids candidatos.

Destino atual: Emotion Engine V1.

### V0.6 — Autonomia

Escopo histórico:

- drives;
- Utility;
- BDI;
- hysteresis;
- arbitragem de Social Bids.

Destino atual: Autonomy Engine V1.

### V0.7 — Ações

Escopo histórico:

- plugins;
- tools;
- planner;
- skills;
- execution monitor.

Destino atual: action/plugin infrastructure V1.

### V0.8 — Conversa natural

Escopo histórico:

- voice pipeline;
- Turn Manager;
- backchannels;
- autonomia social;
- conversa/streaming inicial.

Destino atual: Conversation + Cognition V1.

---

## 3. O que aconteceu com a V0.9 antiga

A V0.9 antiga concentrava avatar/corpo/customização e o planejamento posterior V0.95 concentrava percepção avançada.

Durante a evolução da arquitetura, esse pacote ficou grande demais e misturava três problemas diferentes:

1. corpo/percepção visual;
2. identidade estética persistente;
3. fechamento cognitivo/voz da V1.

A decisão atual foi **dividir**, não descartar.

### Mapa de migração

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

O que já existia como infraestrutura/experimento de avatar pode continuar no repositório, mas **não obriga o fechamento V1.0 a terminar o corpo final**.

---

## 4. V1.0 — fechamento atual

**Status:** `IN_PROGRESS`

Objetivo: fechar cognição, diálogo, voz, provider resilience, Android e certificação sem reabrir escopo visual futuro.

### Já faz parte da fundação V1

- Context Compiler;
- Conversation History literal;
- Cognitive Journal;
- streaming cognição → fala;
- barge-in/cancellation;
- structured proposals atrás de gates locais;
- autonomia;
- routing/quota/backoff/failover;
- reflection;
- Cognitive Replay;
- privacy/consent de processamento remoto;
- backup/restore/observabilidade;
- local Dialogue Orchestrator;
- provider como realização linguística;
- Android app/pareamento/foreground voice;
- server console/health infrastructure.

### Fechamento em quatro passos

#### V1 Step 1 — Local Voice

**Status:** `IN_PROGRESS`

Objetivo:

- VoiceIdentity pt-BR fixa;
- sherpa-onnx + TTS local Piper/VITS provisório;
- Voice Pack versionado + SHA-256;
- síntese offline;
- Gemini TTS fora do caminho normal;
- Android TTS somente bootstrap/emergência;
- benchmark/certificação física no Galaxy A15.

Critério de saída:

- conversa prolongada sem chamada TTS remota;
- mesmo timbre entre turnos;
- pt-BR aceitável;
- latência/CPU/RAM/temperatura aceitáveis;
- barge-in funcional.

#### V1 Step 2 — Dialogue Identity

**Status:** `PLANNED`

Objetivo:

- atos locais de opinião/discordância/zoeira/reclamação/reação relacional/limite;
- stance e relational attitude;
- closing mode;
- validator estrutural pós-geração;
- correção da janela de addressee que não pode se auto-renovar;
- Cognitive Replay comparando modelos para realização linguística.

Critério de saída:

- reduzir comportamento de atendimento genérico;
- provider respeitar `ask_followup` / `offer_help`;
- self-facts grounded;
- identidade social observável em testes físicos.

#### V1 Step 3 — Zero-Cost Provider Mesh

**Status:** `PLANNED`

Objetivo:

- Groq como provider principal enquanto aprovado;
- Cloudflare Workers AI como segundo eixo gratuito/failover;
- OpenRouter como specialist broker / último recurso;
- quota, cooldown, circuit breaker e failover por task class;
- providers pagos não necessários ao uso casual.

Critério de saída:

- falha/quota de um provider não interrompe identidade;
- uso e seleção observáveis;
- rotas remotas respeitam privacy gate;
- custo normal tende a R$ 0.

#### V1 Step 4 — Canonical Voice + V1 Certification

**Status:** `PLANNED`

Objetivo:

- promover `amora-voice-v1` própria quando dataset/direitos/qualidade estiverem resolvidos;
- teste prolongado no A15;
- conversa, barge-in, quota, failover, restart, backup/restore;
- estabilidade térmica e de memória;
- fechamento documental.

---

## 5. V1.1 — percepção e corpo

**Status:** `PLANNED`

Originado principalmente de partes antigas de V0.9/V0.95.

Escopo:

- percepção visual;
- screen mapping;
- seleção de keyframes/OCR/scene changes;
- corpo/avatar final;
- renderer/rig/expressões necessários;
- integração Mind → Avatar;
- presença visual final mobile/stream quando aplicável.

Não transformar V1.1 automaticamente em editor profissional de Live2D/rig completo.

---

## 6. V1.2 — estética e autoimagem

**Status:** `PLANNED`

Originado de ideias antigas de customização que exigem estado persistente próprio, não apenas UI de troca de roupa.

Escopo:

- preferências estéticas persistentes;
- desejo de alterar aparência;
- escolha/avaliação de roupa;
- relação com a própria imagem;
- self-image ownership;
- evolução estética coerente com identidade/memória.

Essas capacidades devem ser da Amora, não simplesmente presets controlados pelo provider.

---

## 7. Futuro / backlog

Possibilidades futuras sem compromisso de versão ainda:

- plugins de games;
- Minecraft/servidor privado/mod/bridge;
- navegador;
- Discord;
- OBS/PRISM avançado;
- automações externas;
- modelos RP especializados;
- TTS local zero-shot/voice cloning alternativo;
- Pocket TTS ou outros backends após benchmark;
- streaming próprio apenas se houver motivo real;
- ferramentas especializadas via OpenRouter.

Nada desta seção deve entrar automaticamente na V1/V1.1/V1.2.

---

## 8. Regra para mudanças futuras

Quando uma decisão de versão mudar:

1. não apagar silenciosamente o item antigo;
2. atualizar este arquivo com `origin_version`, novo destino e motivo;
3. criar/atualizar ADR se a mudança for relevante;
4. atualizar GDD/Architecture se a visão atual mudar;
5. manter histórico Git.

Exemplo:

```text
Feature: estética persistente
Origin: V0.9 planning
Old target: V0.9
New target: V1.2
Status: MOVED
Reason: separar corpo/render de preferência/autoimagem persistente
```

Isso evita que chats futuros “redescubram” um plano antigo e o tratem como novidade ou restaurem uma decisão já abandonada.

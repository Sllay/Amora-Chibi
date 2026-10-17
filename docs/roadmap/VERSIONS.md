# AMORA — Version Lineage / Roadmap Canônico

**Status:** CANÔNICO  
**Atualizado em:** 2026-10-16

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

**Status:** `IN_PROGRESS — STEP 4 CANONICAL VOICE / PHYSICAL SIGNATURE CERTIFICATION`

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

Faber cumpriu o papel de voz técnica que certificou o caminho offline. Long-run barge-in/térmica/conversa permanecem no gate final V1.

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

**Status:** `IMPLEMENTED / MERGED`

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

PR #30 foi integrado ao `main`; o provider mesh faz parte da fundação V1 atual.

### V1 Step 4 — Canonical Voice + V1 Certification

**Status:** `IN_PROGRESS / BASE PHYSICALLY SELECTED / PRODUCTION SIGNATURE IMPLEMENTED / APK ACCEPTANCE PENDING`

Decisão atual segue `docs/canon/VOICE_IDENTITY.md` + ADR-0009.

O treino/fine-tuning próprio deixou de ser requisito da V1. A identidade agora é:

```text
Supertonic 3 INT8 local
↓
speaker 0 fisicamente selecionado
↓
VoiceSignature AMORA_CANONICAL
↓
SpeechStyle / Emotion Engine bounded
↓
PCM / AudioTrack
```

Entregue nesta etapa:

- Voice Lab Android isolado;
- Supertonic 3 INT8 via sherpa-onnx;
- múltiplos speakers comparados fisicamente no Galaxy A15;
- **speaker 0 selecionado por BK como base canônica**;
- `LocalVoiceEngine` de produção migrado para speaker 0;
- VoiceSignature V1 com EQ/cor tonal, saturação suave, textura harmônica discreta, compressor e limiter;
- `SpeechStyle` agora entrega rate/pitch-signal/volume/valence/arousal ao Android;
- Emotion Engine continua autoridade emocional e modula a expressão sem trocar identidade;
- profile versionado `amora_voice_profile.json` servido pelo Core;
- Android revalida profile com ETag, guarda último válido e continua offline em falha;
- tuning de assinatura/perfis pode mudar no Core sem recompilar APK;
- profiles `NATURAL`, `SOFT`, `SHY`, `SYNTH`, `CHIBI` agora possuem parâmetros realmente distintos;
- identidade canônica bloqueia troca silenciosa de backend/speaker pelo profile;
- testes de contrato Core/Android adicionados para speaker 0, bounds, hot profile e DSP determinístico.

O backend Supertonic integrado hoje não possui controle nativo de emoção comprovado pelo adapter sherpa utilizado. Portanto V1 não finge suporte inexistente: a emoção é aplicada pelo cérebro/`SpeechStyle` e pela camada bounded de assinatura. Um adapter futuro pode usar emoção nativa real se outro backend a expuser e passar gate físico.

Próximos gates, sem nova fase de protótipo:

1. CI Python + Android da branch de produção;
2. instalar uma build estrutural `1.0.0-dev8-amora-voice` no Galaxy A15;
3. validar qualidade de `NATURAL` e distinguir `SOFT`/`SHY`/`SYNTH`/`CHIBI`;
4. tunar `amora_voice_profile.json` via Core/hot reload, sem rebuild de APK;
5. validar emoção real da conversa, pronúncia, artefatos, RTF/latência/RAM/CPU/temperatura;
6. conversa longa, barge-in, restart e offline;
7. remover assets Faber do pacote após aceite do novo caminho, se nenhuma compatibilidade exigir sua permanência;
8. fechamento documental V1.

Treino próprio pode ser revisitado no futuro, mas não bloqueia a V1.

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

**Status:** `PLANNED — IMEDIATAMENTE APÓS FECHAMENTO DA VOZ/V1`

Objetivo: permitir uso diário no iPhone sem depender de APK/compilação Android para cada iteração.

Direção:

- cliente Web/PWA instalável pela Tela de Início do Safari;
- UI web separada do Core;
- `platform adapters` para áudio/storage/microfone/web em vez de duplicar lógica da Amora;
- mesma identidade/estado canônico compartilhados entre Android e Web;
- VoiceSignature especificada de forma portável quando tecnicamente viável;
- frontend estático pode usar hosting como Render Static Site ou equivalente;
- modelos/voice packs web podem ser baixados na primeira execução e armazenados localmente quando a plataforma suportar;
- não tratar site/PWA como "APK rodando no navegador"; é outro cliente sobre contratos comuns.

### Core remoto / disponibilidade contínua

Depois do cliente PWA, avaliar separação entre frontend e Core persistente remoto de custo zero/baixo.

Candidatos arquiteturais incluem Cloudflare Workers/Durable Objects/D1 ou equivalentes, desde que passem gates de:

- persistência real;
- backup/restore;
- privacidade;
- custo previsível/zero-cost guard;
- latência;
- ausência de dependência de celular ligado;
- capacidade de manter identidade/memória fora do provider cognitivo.

**Importante:** cognição por API continua sendo provider; hospedar o Core não significa hospedar o LLM.

Nenhuma migração do Core Termux deve ocorrer antes de prova de persistência/backup e ADR específico.

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

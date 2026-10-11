# PROJECT_STATE — Amora-Chibi

**Última atualização:** 2026-10-10  
**Objetivo:** permitir continuidade segura entre chats/agentes sem depender do histórico integral da conversa.

Este arquivo é um **snapshot operacional**. Para regras atuais, sempre leia primeiro `docs/canon/*`.

---

## 1. Fonte de verdade

Ordem de leitura:

1. `docs/canon/SKILL.md`;
2. `docs/canon/ARCHITECTURE.md`;
3. `docs/canon/GDD.md`;
4. `docs/roadmap/VERSIONS.md`;
5. `docs/decisions/`;
6. este `PROJECT_STATE.md`;
7. docs técnicos específicos de versão/módulo.

Docs antigos não são autoridade atual quando divergirem do canon.

---

## 2. Invariante de identidade

```text
Provider = capacidade cognitiva/linguística substituível
Amora Core = identidade + estado + memória + relações + emoção + drives + intenção + causalidade
```

A Amora não é um LLM.

Trocar Groq/Gemini/Cloudflare/OpenRouter/outro provider não pode trocar `ENTITY_AMORA`.

---

## 3. Plataforma

Host principal atual:

```text
Galaxy A15 5G
├── Termux / Python → Amora Core
└── Android / Kotlin → microfone, áudio, UI, foreground service, pairing e voz local
```

Backend: modular monolith, SQLite, asyncio/FastAPI, Event Bus e APIs locais.

---

## 4. Estado técnico V1 já integrado

### Persistência cognitiva

Schema atual da linha V1 inclui estruturas como:

- `conversation_messages`;
- `cognitive_journal`;
- `speech_segments`;
- `model_usage`.

Restart e backup/restore devem preservar estado cognitivo persistente.

### Context Compiler

Compila somente contexto relevante a partir de:

- Self Model;
- relationships;
- mood/affect;
- drives;
- active intention;
- memories;
- World Model;
- recent literal conversation;
- privacy/remote authorization;
- DialoguePlan.

### Histórico de diálogo

O turno humano atual deve aparecer uma única vez para o provider.

Conversation History usa papéis nativos (`user/assistant`, `user/model`) em vez de achatar tudo em prompt.

Segmentos de uma mesma resposta da Amora devem ser recompostos como um turno lógico para histórico.

### Dialogue Orchestrator

O Core possui `DialoguePlan` local e autoritativo.

Atos existentes incluem:

- acknowledgement;
- self fact;
- disclosure;
- support request;
- answer question;
- continue topic;
- autonomous initiation;
- reflection.

Self-facts não definidos permanecem desconhecidos; provider não inventa idade/data/origem.

Próximo refinamento da V1 deve adicionar opinião/stance/reação relacional/closing mode e validator estrutural para reduzir comportamento genérico de atendimento.

### Streaming cognition / speech

- providers podem gerar incrementalmente;
- `SpeechSegmenter` libera cláusulas estáveis;
- `SpeechNormalizer` separa display/fala/nonverbal;
- segmentos persistem antes da entrega;
- estados incluem queued/speaking/spoken/interrupted/failed.

### Barge-in / cancellation

- Android monitora interrupção humana;
- provider/turno pode ser cancelado;
- não criar segmentos novos após cancelamento;
- não costurar provider diferente depois que fala já começou;
- entrega restante é interrompida/cancelada.

### Structured reflection / proposals

Provider pode propor, mas gates locais decidem:

- memória;
- belief hypothesis;
- action suggestion;
- tone metadata.

Structured output do provider é não confiável até passar por validação local.

### Provider resilience

Linha atual inclui:

- Groq primary;
- Gemini cognitive fallback quando configurado;
- local fallback;
- quota/cooldown/backoff;
- failover antes de fala emitida;
- Cognitive Replay sem mutação do estado.

Direção aprovada para Step 3:

- adicionar Cloudflare Workers AI;
- usar OpenRouter como specialist broker / último recurso;
- manter objetivo operacional R$ 0 sempre que praticável.

### Privacidade

Android possui autorização explícita para cognição remota, separada de autorização de STT/áudio.

AUTH nunca vai para provider remoto.

Disclosure e Remote Processing continuam políticas diferentes.

### Observabilidade

O servidor possui console operacional vivo para evitar spam infinito de polling e mostrar estados/módulos/providers/plugins.

Logs estruturados completos permanecem separados do painel humano.

---

## 5. Incidentes/decisões recentes importantes

### Gemini TTS quota

Uso físico revelou `429 RESOURCE_EXHAUSTED` no Gemini TTS após poucas requisições do free tier.

Problema não era apenas indisponibilidade: fallback para Android TTS mudava a identidade vocal para uma voz muito robótica.

Decisão: **Gemini TTS sai do caminho normal da voz**. Ver `docs/decisions/ADR-0002-local-voice-as-normal-path.md`.

### Android TTS / delivery loop

Houve estado em que falha/indisponibilidade de TTS podia reentregar a mesma utterance repetidamente.

A linha V1 recebeu hardening de readiness/fallback; qualquer nova recorrência deve ser tratada como delivery-state bug, não mascarada com retry infinito.

### Console server

Polling HTTP repetitivo e tracebacks idênticos tornavam o terminal inutilizável.

Direção vigente: painel vivo com estado/health + logs forenses separados.

### Comportamento “chatbot”

Mesmo com `offer_help=false` / `ask_followup=false`, modelos gerais podem produzir reflexos de atendimento.

Decisão: não resolver com prompt infinito/blacklist. Expandir semântica local + validator e comparar realizadores linguísticos por Cognitive Replay.

---

## 6. Fechamento V1 — quatro passos

### Step 1 — Local Voice

**Status operacional:** `IN_PROGRESS`.

PR ativo no momento deste snapshot:

```text
#27 feat(v1.0): make Amora voice local and stable
```

Objetivo do PR:

- `VoiceIdentity` pt-BR fixa;
- sherpa-onnx `OfflineTts`;
- Piper/VITS pt-BR provisório;
- Voice Pack baixado uma vez;
- verificação SHA-256;
- PCM local via AudioTrack;
- Gemini TTS removido do caminho normal;
- Android TTS apenas bootstrap/emergência;
- Android `1.0.0-dev4` / versionCode 7.

**Importante:** compilação/CI não equivale a certificação física.

Gate físico pendente no Galaxy A15:

- primeiro download/instalação;
- checksum;
- voz pt-BR;
- latência;
- RTF;
- RAM/CPU/temperatura;
- barge-in;
- conversa prolongada;
- confirmar zero requests de TTS remoto após pack ativo.

### Step 2 — Dialogue Identity

**Status:** `PLANNED`.

- atos sociais ricos;
- stance/opinião;
- relational attitude;
- closing mode;
- validator estrutural;
- corrigir continuidade de addressee que não pode se auto-renovar infinitamente;
- comparar GPT-OSS/Qwen/outros por Cognitive Replay antes de trocar provider.

### Step 3 — Zero-Cost Provider Mesh

**Status:** `PLANNED`.

- Groq;
- Cloudflare Workers AI;
- OpenRouter specialist/last resort;
- quota/cooldown/circuit breaker;
- provider certification por task class.

### Step 4 — Canonical Voice + V1 Certification

**Status:** `PLANNED`.

- promover voz própria quando direitos/dataset/qualidade estiverem resolvidos;
- certificação longa no A15;
- restart;
- backup/restore;
- quota/failover;
- conversa longa;
- estabilidade térmica/memória.

Não abrir o próximo step com defeito estrutural aberto no anterior.

---

## 7. Roadmap posterior

### V1.1

- percepção visual;
- screen mapping;
- corpo/avatar final;
- renderer/rig/expressões necessários;
- integração semântica Mind → Avatar.

### V1.2

- desejo estético persistente;
- escolhas de aparência;
- avaliação de roupa;
- preferências visuais;
- propriedade sobre autoimagem.

Planejamento antigo V0.9/V0.95 foi dividido. Esses itens foram movidos, não cancelados.

---

## 8. Git / Android workflow

BK usa GitSync para atualizar o diretório compartilhado e não quer credenciais GitHub no Termux.

Fluxo Android/Core:

```bash
bash /storage/emulated/0/Documents/Amora-Chibi/tools/termux-sync.sh \
  /storage/emulated/0/Documents/Amora-Chibi

source ~/.venvs/amora/bin/activate
cd ~/amora-src
amora serve --port 8765
```

Não pedir GitHub token no Termux.

Provider secrets ficam no SecretStore local da Amora, nunca em chat/repo.

---

## 9. Regras de continuidade para próximo chat/agente

Antes de sugerir arquitetura nova:

- ler canon;
- ler roadmap;
- procurar ADR;
- verificar PRs ativos;
- não confundir plano antigo com plano atual;
- não reintroduzir Gemini TTS como voz normal;
- não transformar provider em identidade;
- não inventar self-facts;
- não declarar hardware PASS com base em CI;
- não expandir V1 com V1.1/V1.2 sem decisão explícita.

Se a mesma área entrar em patch loop, parar e pesquisar/reavaliar ownership antes de continuar.

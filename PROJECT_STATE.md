# PROJECT_STATE — Amora-Chibi

**Última atualização:** 2026-10-11  
**Objetivo:** permitir continuidade segura entre chats/agentes sem depender do histórico integral da conversa.

Este arquivo é um **snapshot operacional**. Para regras atuais, sempre leia primeiro `docs/canon/*`.

---

## 1. Fonte de verdade

Ordem de leitura:

1. `docs/canon/SKILL.md`;
2. `docs/canon/ARCHITECTURE.md`;
3. `docs/canon/GDD.md`;
4. `docs/canon/DIALOGUE_EMOTION.md`;
5. `docs/canon/VOICE_IDENTITY.md`;
6. `docs/roadmap/VERSIONS.md`;
7. `docs/decisions/`;
8. este `PROJECT_STATE.md`;
9. docs técnicos específicos de versão/módulo.

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

Schema V1 inclui estruturas como:

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

**Defeito estrutural atual:** o plano ainda é enviado ao provider como instrução textual e não existe hard-control completo por `RealizationFrame` + validator. Step 2 corrige isso segundo ADR-0006.

### Emotion Engine

O Core já possui appraisal determinístico, Emotion Events, Global Mood, Entity Affect, decay, blame, intentionality, spillover e Social Bids.

**Defeito estrutural atual:** o fluxo normal de conversa consulta `current_mood()` e affect, mas não está fechado o suficiente para transformar acontecimentos normais de conversa em appraisals persistentes. Isso explica cenários em que `mood_valence` permanece em baseline apesar de conversas longas.

Step 2 deve conectar evento real → appraisal → Emotion Engine → Dialogue/Autonomy/Prosody conforme ADR-0007.

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

Android possui autorização explícita para cognição remota de falas do usuário, separada de autorização de STT/áudio.

`AMORA_REMOTE_COGNITION_ALLOWED` é fallback/controle de cognição remota autônoma quando o evento não traz autorização explícita; não deve substituir o toggle normal do app.

AUTH nunca vai para provider remoto.

Disclosure e Remote Processing continuam políticas diferentes.

### Observabilidade

O servidor possui console operacional vivo para evitar spam infinito de polling e mostrar estados/módulos/providers/plugins.

Logs estruturados completos permanecem separados do painel humano.

Step 2 deve tornar observáveis último appraisal, último Emotion Event, DialoguePlan, RealizationFrame/validator e `test_mode`.

---

## 5. Decisões/incidentes recentes importantes

### Step 1 — voz local: PASS de ativação

PR #27 foi mergeado após:

- CI Core PASS;
- Android build/test PASS;
- modelo Faber empacotado e hash verificado;
- Galaxy A15 reproduzir fisicamente a voz neural Faber via sherpa-onnx.

A causa principal do fallback robótico anterior foi um SHA-256 obsoleto que rejeitava o modelo Faber atual. A dev6 moveu o voice pack para build-time assets e removeu fallback automático oculto para Google/Android TTS.

Faber continua **provisório e masculino**. A voz feminina canônica `amora-voice-v1` fica para Step 4.

### Comportamento “chatbot”

Mesmo com `offer_help=false` / `ask_followup=false`, modelos gerais podem produzir reflexos de atendimento.

Decisão: não resolver com prompt infinito/blacklist. Implementar `RealizationFrame` estruturado + validator local. Ver ADR-0003 e ADR-0006.

### Emoção sem circuito completo

Emotion Engine existe, mas precisa receber appraisals do fluxo normal de conversa/ação. Ler mood sem alimentá-lo é integração parcial.

Decisão: fechar o circuito e adicionar isolamento de testes. Ver ADR-0007 e `docs/canon/DIALOGUE_EMOTION.md`.

### Testes contaminando estado

Baterias técnicas não devem virar biografia, relação ou emoção persistente.

Direção: metadata de teste capaz de desabilitar consolidação de memória, aprendizado relacional e persistência emocional sem impedir o pipeline técnico.

---

## 6. Fechamento V1 — quatro passos

### Step 1 — Local Voice

**Status operacional:** `PASS / MERGED`.

Entregue:

- `VoiceIdentity` pt-BR fixa e provisória;
- sherpa-onnx `OfflineTts`;
- Piper/VITS Faber provisório;
- voice pack empacotado em build-time assets;
- SHA-256 verificado;
- PCM local via AudioTrack;
- Gemini TTS removido do caminho normal;
- sem fallback automático silencioso para Android/Google TTS;
- diagnóstico de voz no app;
- confirmação física de Faber no Galaxy A15.

Barge-in prolongado, estabilidade térmica e regressões de longa duração continuam no gate final de certificação V1, não como bloqueio estrutural de Step 2.

### Step 2 — Dialogue Identity + Emotion Integration

**Status:** `IN_PROGRESS`.

Branch ativa:

```text
feat/v1.0-dialogue-emotion-integration
```

Escopo:

- atos sociais ricos;
- stance/opinião;
- relational attitude;
- closing mode;
- `RealizationFrame` estruturado;
- validator semântico estrutural;
- realização local para atos triviais já resolvidos;
- conectar conversa/eventos reais ao Emotion Engine;
- emoção influenciar comportamento antes de prosódia;
- test-mode sem contaminar memória/relação/mood persistente;
- observabilidade de appraisal/plan/realization;
- corrigir continuidade de addressee que não pode se auto-renovar infinitamente.

### Step 3 — Zero-Cost Provider Mesh

**Status:** `PLANNED`.

- Groq;
- Cloudflare Workers AI;
- OpenRouter specialist/last resort;
- quota/cooldown/circuit breaker;
- provider certification por task class.

### Step 4 — Canonical Voice + V1 Certification

**Status:** `PLANNED / VOZ ESPECIFICADA`.

- seguir `docs/canon/VOICE_IDENTITY.md`;
- obter dataset feminino pt-BR com direitos claros;
- treinar/fine-tunar `amora-voice-v1` fora do A15, priorizando compute gratuito;
- escolher medium/high por qualidade + benchmark, não pelo rótulo;
- export ONNX e empacotar no mesmo runtime sherpa-onnx;
- certificação longa no A15;
- restart;
- backup/restore;
- quota/failover;
- conversa longa;
- estabilidade térmica/memória.

Treino pago não é requisito operacional. Se compute pago algum dia for avaliado, precisa de teto explícito; GPU gratuita é preferência.

---

## 7. Regra de testes daqui para frente

Um teste só deve existir se o resultado:

1. decidir entre arquiteturas reais;
2. provar um gate físico/hardware;
3. detectar/prevenir regressão crítica;
4. validar contrato que entra em produção.

Evitar builds/experimentos descartáveis apenas por curiosidade quando não alteram decisão de projeto.

---

## 8. Roadmap posterior

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

## 9. Git / Android workflow

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

## 10. Regras de continuidade para próximo chat/agente

Antes de sugerir arquitetura nova:

- ler canon;
- ler roadmap;
- procurar ADR;
- verificar PRs ativos;
- não confundir plano antigo com plano atual;
- não reintroduzir Gemini TTS como voz normal;
- não transformar provider em identidade;
- não inventar self-facts;
- não tratar blacklist como controle social principal;
- não mapear emoção diretamente para pitch/volume como comportamento;
- não deixar testes técnicos consolidarem estado persistente por padrão;
- não declarar hardware PASS com base apenas em CI;
- não expandir V1 com V1.1/V1.2 sem decisão explícita.

Se a mesma área entrar em patch loop, parar e pesquisar/reavaliar ownership antes de continuar.

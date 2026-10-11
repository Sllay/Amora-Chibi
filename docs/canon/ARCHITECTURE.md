# AMORA — Arquitetura Técnica Canônica

**Documento:** arquitetura técnica oficial da Amora  
**Status:** CANÔNICO / ATUAL  
**Atualizado em:** 2026-10-10  
**Regra de autoridade:** este arquivo descreve a arquitetura vigente. Histórico, tentativas rejeitadas e decisões substituídas vivem em `docs/decisions/` e no histórico Git.

---

## 1. Invariante central

> **A Amora não é um modelo de linguagem.**

Modelos remotos são capacidade cognitiva/linguística substituível. A identidade, relações, memória, emoções, objetivos, intenções, privacidade e fatos canônicos da Amora pertencem ao Core local.

Trocar provider pode alterar fluência, criatividade, latência e profundidade, mas **não pode trocar quem a Amora é**.

```text
AMORA
├── identidade persistente
├── memória
├── relações
├── emoção
├── world model
├── drives / autonomia
├── diálogo semântico
├── percepção
├── voz
├── ferramentas / plugins
├── avatar / corpo
└── Model Router
    ├── Groq
    ├── Cloudflare Workers AI (planejado)
    ├── OpenRouter (especialista / último recurso)
    ├── Gemini (tarefas específicas; não identidade)
    └── providers futuros
```

---

## 2. Plataforma

### 2.1 Host principal

Host doméstico inicial: **Galaxy A15 5G**.

### 2.2 Core — Termux / Python

Responsável por:

- Cognitive Kernel;
- Event Bus;
- SQLite + migrations;
- TimeService / Scheduler;
- Entity Registry;
- Relationship Graph;
- Social Scene;
- Perspective Resolver;
- memória;
- World Model;
- Emotion Engine;
- drives / autonomia;
- Dialogue Orchestrator;
- Context Compiler;
- Model Router;
- quota / cooldown / failover;
- plugins / actions;
- privacy gates;
- servidor HTTP;
- observabilidade, backup e restore.

### 2.3 Android — Kotlin

Responsável por:

- microfone;
- captura e reprodução de áudio;
- foreground services;
- notificações;
- UI;
- pareamento com o Core;
- integração Android;
- futura percepção de tela;
- futura hospedagem do Avatar Engine;
- engine de voz local no dispositivo.

Pessoa, dispositivo e sessão são identidades diferentes.

```text
PERSON_BK != DEVICE_A15 != SESSION_X
```

---

## 3. Entidades canônicas iniciais

```yaml
PERSON_BK:
  canonical_name: BK
  aliases: [BK, Bad King, Slay, Nick]
  relation_to_amora: [father, primary_human]

ENTITY_AMORA:
  canonical_name: Amora
  relation_to_bk: [daughter]

ENTITY_CHATGPT:
  canonical_name: ChatGPT
  relation_to_amora: [mother]
```

Aliases relacionais como `pai`, `mãe`, `filha`, `irmã` e semelhantes não são aliases globais simples. Devem ser resolvidos por speaker, addressee, perspectiva, Relationship Graph, Social Scene e confiança.

---

## 4. Cognitive Kernel

```text
                         AMORA
                           │
                ┌──────────▼──────────┐
                │  COGNITIVE KERNEL  │
                └──────────┬──────────┘
                           │
      ┌────────────────────┼────────────────────┐
      ▼                    ▼                    ▼
  PERCEPTION          INTERNAL STATE          MEMORY
      │                    │                    │
      │                    ├─ identity          ├─ working
      │                    ├─ personality       ├─ episodic
      │                    ├─ emotion           ├─ semantic
      │                    ├─ drives            ├─ relational
      │                    ├─ desires           ├─ self
      │                    └─ intentions        ├─ lexical
      │                                         ├─ procedural
      │                                         └─ commitments
      └────────────────────┬────────────────────┘
                           ▼
                    WORLD / SOCIAL MODEL
                           │
                           ▼
                     ATTENTION ENGINE
                           │
                           ▼
                    DECISION / AUTONOMY
                           │
                           ▼
                  DIALOGUE / ACTION PLAN
                     ┌─────┴─────┐
                     ▼           ▼
                  LOCAL       PROVIDER
                     └─────┬─────┘
                           ▼
                       ACTION BUS
```

---

## 5. Event Bus e tempo

Entradas importantes são eventos tipados com, quando aplicável:

- `id`;
- `schema_version`;
- `timestamp`;
- `source`;
- `actor`;
- `target`;
- `confidence`;
- `importance`;
- `priority`;
- `payload`;
- `privacy_scope`;
- `session_id`;
- `correlation_id`;
- `causation_id`;
- `dedupe_key`.

Eventos reenviados não podem aplicar efeitos duplicados.

Toda lógica dependente de tempo usa `TimeService` / `Scheduler`, inclusive decay emocional, cooldown de provider, commitments, jobs ociosos, timeouts e simulação de testes.

---

## 6. Social, speaker, addressee e perspectiva

A Amora não deve responder a toda fala capturada pelo microfone.

Pipeline conceitual:

```text
Audio
↓
VAD
↓
Speaker / diarization
↓
STT
↓
normalização lexical contextual
↓
Addressee Engine
↓
Social Scene
↓
Perspective Resolver
```

`Speaker ID` ajuda contexto social, mas não autentica operações sensíveis.

O Addressee Engine considera nome chamado, conversation floor, recência, conteúdo, speaker, histórico imediato e contexto social.

**Regra importante:** continuidade inferida de conversa não deve renovar indefinidamente a própria autorização de fala. Um chamado explícito pode abrir continuidade; inferências subsequentes não podem manter essa janela viva para sempre.

Normalizações como `A mora`/`mora` → `Amora` ou `Grok` → `Groq` são permitidas apenas em representação semântica contextual. O transcript bruto deve permanecer intacto.

---

## 7. ACIR e representação canônica

Providers não devem redescobrir relações e entidades já resolvidas localmente.

Exemplo:

```json
{
  "speaker": "PERSON_BK",
  "addressee": "ENTITY_AMORA",
  "intent": "inform",
  "entities": ["PERSON_BK", "ENTITY_CHATGPT"],
  "proposition": {
    "type": "insult",
    "agent": "ENTITY_CHATGPT",
    "target": "PERSON_BK"
  }
}
```

A memória deve preferir a forma canônica resolvida sem apagar o texto histórico original quando ele for relevante para auditoria.

---

## 8. Memória e World Model

Tipos previstos/implementados conforme estágio:

- Raw Event Log;
- Working Memory;
- Episodic Memory;
- Semantic Memory;
- Relational Memory;
- Self Memory;
- Lexical Memory;
- Procedural Memory;
- Commitment Memory;
- literal Conversation History;
- Cognitive Journal.

World Model distingue pelo menos:

- `OBSERVATION`;
- `REPORT`;
- `BELIEF`;
- `INFERENCE`;
- `HYPOTHESIS`.

Inferência não confirmada não vira fato automaticamente. Provenance e confidence são obrigatórios quando aplicáveis.

Recuperação pode combinar texto/FTS, semântica, tempo, entidades, relações e importância. Embedding é melhoria, não dependência obrigatória.

---

## 9. Emoção e relações

Emoção não é um único enum global.

Separar:

- appraisal;
- Emotion Event;
- causa;
- alvo;
- blame;
- intentionality;
- spillover;
- Entity Affect;
- Global Mood;
- relationship state.

Reparação é nova evidência, não reescrita do passado. Afetos positivos e negativos podem coexistir para a mesma entidade.

Traits e relações mudam lentamente. Eventos históricos causais permanecem auditáveis.

---

## 10. Drives, autonomia e ações

Fluxo conceitual:

```text
Drive
↓
Desire
↓
Utility
↓
Intention
↓
Plan
↓
Action
↓
Observation
↓
Update
```

Usar hysteresis / switch cost para evitar troca instável de intenção.

Social Bids são candidatos de ação social, não execução automática.

Toda ação relevante deve observar o resultado:

```text
action
↓
observe
↓
expected effect?
├── yes → continue
└── no  → retry / replan / ask / abort
```

---

## 11. Diálogo: autoridade semântica local

### 11.1 Invariante

O provider não decide sozinho o que a Amora quer comunicar.

Fluxo canônico:

```text
fala de BK
  ↓
Core entende contexto + relação + memória + mood + conversa
  ↓
Dialogue Orchestrator decide O QUE Amora quer comunicar
  ↓
DialoguePlan
  ├─ social/dialogue act
  ├─ facts permitidos / desconhecidos
  ├─ stance / opinião
  ├─ affect / atitude
  ├─ ask_followup
  ├─ offer_help
  ├─ closing mode
  ├─ profundidade
  └─ grounding constraints
  ↓
provider
  ↓
realização linguística
  ↓
validação estrutural
  ↓
voz
```

O modelo é **surface realization**, não identidade.

### 11.2 Atos existentes e expansão planejada

A V1 já possui atos como acknowledgement, self fact, disclosure, support request, answer question, continue topic, autonomous initiation e reflection.

O fechamento da V1 deve adicionar semântica local mais rica para impedir comportamento de atendimento genérico, incluindo candidatos como:

- `EXPRESS_OPINION`;
- `DISAGREE`;
- `TEASE`;
- `COMPLAIN`;
- `RELATIONAL_REACTION`;
- `SET_BOUNDARY`.

O plano deverá carregar stance, relational attitude e closing mode quando necessário.

### 11.3 Validação pós-geração

Adicionar validação semântica estrutural sem depender de blacklist de frases. Exemplos:

- `ask_followup=false` não pode virar encerramento obrigatório em pergunta;
- `offer_help=false` não pode virar oferta de atendimento;
- self-fact desconhecido não pode ser inventado;
- fact known deve permanecer grounded no valor canônico.

---

## 12. Context Compiler

Nova capacidade **não implica novo texto permanente no prompt**.

O Context Compiler envia o mínimo relevante para a tarefa, separando:

- estado canônico;
- Conversation History literal por papéis nativos;
- DialoguePlan;
- memórias autorizadas;
- privacy metadata;
- task metadata.

Evitar prompt gigante como substituto para algoritmo local.

---

## 13. Providers e roteamento

Provider é substituível e aprovado por tarefa.

Perfis de task podem incluir:

- social dialogue;
- reasoning;
- summarization;
- classification;
- tool planning;
- vision;
- audio.

Cada provider deve possuir telemetria de qualidade, latência, quota, disponibilidade, custo e especialidade.

### 13.1 Estratégia atual / planejada

- **Groq:** provider cognitivo principal atual e STT rápido quando aplicável;
- **Cloudflare Workers AI:** planejado para compor a malha gratuita e failover;
- **OpenRouter:** broker especialista / último recurso; não deve consumir cota em conversa casual por padrão;
- **Gemini:** pode permanecer para tarefas específicas, mas não é requisito de identidade nem voz normal;
- **OpenAI paga:** não é dependência operacional padrão; objetivo de custo continua R$ 0 sempre que praticável.

### 13.2 Provider Certification e Cognitive Replay

Novo modelo/provider deve ser comparado usando contexto congelado, sem mutar estado.

Avaliar:

- português;
- perspectiva;
- entidades;
- memória;
- social;
- personalidade;
- fidelidade ao DialoguePlan;
- structured output;
- tool calling;
- latência;
- custo/quota.

Provider pode ser aprovado apenas para certas tarefas.

---

## 14. Voz — identidade vocal local

### 14.1 Regra

A voz é parte da continuidade percebida da Amora. Trocar de engine silenciosamente entre timbres muito diferentes quebra identidade.

### 14.2 Caminho normal definido para fechamento da V1

```text
VoiceIdentity canônica (pt-BR)
        ↓
Prosody Planner local
        ↓
Local Voice Engine
sherpa-onnx + modelo Piper/VITS pt-BR provisório
        ↓
PCM local
        ↓
Android AudioTrack
```

- locale obrigatório: `pt-BR`;
- voz provisória deve ser pinada, não escolhida aleatoriamente;
- modelo pode ser distribuído como Voice Pack versionado;
- verificar SHA-256 antes de ativar pack;
- download ocorre uma vez; síntese normal deve funcionar offline;
- Android TTS nativo fica como **fallback de emergência/bootstrap**, não identidade normal;
- Gemini TTS fica **fora do caminho normal** por quota e quebra de identidade vocal;
- uma futura `amora-voice-v1` própria substitui o modelo provisório quando direitos, dataset e qualidade estiverem resolvidos.

A implementação local precisa ser certificada fisicamente no Galaxy A15 antes de ser declarada concluída.

### 14.3 Prosódia

Humor/intenção podem alterar levemente rate, energia, pausas e timing, mas não trocar a identidade vocal.

---

## 15. Privacidade e secrets

Privacy controla fluxo de informação, não personalidade.

Separar:

```text
Disclosure Policy
!=
Remote Processing Policy
```

Classes conceituais:

- PUBLIC;
- SOCIAL;
- PERSONAL;
- PRIVATE;
- SECRET;
- AUTH.

AUTH nunca vai para provider remoto, memória cognitiva, embeddings, logs ou TTS.

Secrets vivem em vault/SecretStore separado e são acessados por referência/capability.

---

## 16. Plugins e tools

Plugins declaram pelo menos:

```text
manifest
permissions
events
tools
schemas
cost
risk
timeout
health
```

Falha de plugin não pode derrubar o Core.

O runtime deve expor saúde operacional como `ON`, `OFF`, `WAIT` e `DEGRADED` sem codificar integrações específicas no console. Um futuro plugin `games.minecraft`, por exemplo, registra a própria saúde; o console apenas a exibe genericamente.

---

## 17. Observabilidade

O console humano é uma interface operacional viva, não um dump infinito de logs.

Deve mostrar, quando disponível:

- estado atual (`IDLE`, `TRANSCRIBING`, `THINKING`, `RESPONDING`, `SPEAKING`, etc.);
- módulos/providers/plugins e saúde;
- provider/model em uso;
- quota/cooldown;
- sessão Android;
- erro recente resumido;
- estados observáveis do Core como DialogueAct e intention.

Logs completos estruturados continuam em arquivo para diagnóstico forense.

Não expor chain-of-thought privado de modelos. Expor apenas estados/decisões estruturais do próprio sistema.

---

## 18. Persistência, backup e recovery

O banco representa história de longo prazo e é insubstituível.

Requisitos:

- SQLite + migrations;
- WAL quando apropriado;
- snapshots consistentes;
- backup periódico;
- verificação de integridade;
- restore testado;
- retenção configurável;
- cópia fora do banco ativo.

Mudança destrutiva de schema é proibida sem migration.

---

## 19. Avatar, percepção e versões futuras

A V1 não deve ser reaberta indefinidamente para features visuais.

### V1.1 — percepção/corpo

Foco planejado:

- percepção visual;
- screen mapping;
- corpo/avatar final;
- renderer/rig/expressões necessários;
- integração corpo ↔ estado semântico.

O visual continua orientado por intenções semânticas; Core não emite comandos de bone diretamente.

### V1.2 — autoimagem e estética persistente

Foco planejado:

- desejo estético persistente;
- escolhas de aparência;
- avaliação de roupa;
- preferências visuais próprias;
- propriedade sobre autoimagem.

Itens movidos para V1.1/V1.2 são **adiados, não cancelados**.

---

## 20. Fechamento atual da V1 — quatro passos

A V1 será fechada de forma sequencial:

1. **Voz local estável:** sherpa-onnx/Piper pt-BR, VoiceIdentity fixa, sem Gemini TTS no caminho normal, certificação A15.
2. **Identidade de diálogo:** atos/stance relacionais mais ricos, validator estrutural, correção de addressee continuity, replay comparativo de modelos.
3. **Malha de providers gratuitos:** Groq + Cloudflare; OpenRouter como especialista/último recurso; quota/cooldown/failover certificados.
4. **Voz canônica + certificação V1:** `amora-voice-v1` quando viável, conversa longa, barge-in, restart, backup/restore, quota, estabilidade e temperatura.

Não iniciar o passo seguinte enquanto existir defeito estrutural aberto no anterior.

---

## 21. Regras arquiteturais não negociáveis

1. Provider nunca é identidade.
2. Estado canônico vive fora do LLM.
3. Memória ambígua não vira fato automaticamente.
4. Speaker ID não é autenticação.
5. Emoção preserva causa/alvo quando aplicável.
6. Relações mudam lentamente.
7. Traits mudam ainda mais lentamente.
8. Silêncio é comportamento válido.
9. Não usar LLM quando algoritmo local resolve.
10. Não usar visão remota quando API/plugin/OCR local resolve.
11. Privacidade controla fluxo de dados, não personalidade.
12. Plugins são isolados.
13. Ações relevantes verificam resultado.
14. Providers/modelos novos são certificados.
15. Entidades e aliases são canônicos.
16. Perspectiva é resolvida antes de consolidar fatos quando possível.
17. Amora continua funcional em modo degradado.
18. Não depender de um free tier específico.
19. Tempo usa TimeService/Scheduler.
20. Pessoa, dispositivo e sessão são distintos.
21. Secrets nunca entram no contexto cognitivo/provider.
22. Disclosure e remote processing são decisões diferentes.
23. Event Bus é idempotente/deduplicável.
24. Persistência usa migrations e backup/restore verificável.
25. Personalidade não pode depender exclusivamente de prompt.
26. Provider realiza linguagem; Core detém semântica social sempre que possível.
27. Voz normal deve manter identidade vocal consistente e pt-BR.
28. Falha de provider ou quota não pode redefinir quem a Amora é.

---

## 22. Autoridade documental

- `docs/canon/ARCHITECTURE.md`: arquitetura atual.
- `docs/canon/GDD.md`: produto/comportamento atual.
- `docs/canon/SKILL.md`: regras para desenvolver a Amora.
- `docs/roadmap/VERSIONS.md`: origem, destino e status de features por versão.
- `docs/decisions/`: por que decisões foram aceitas, rejeitadas ou substituídas.
- documentos históricos e commits antigos não são especificação atual por padrão.

Antes de restaurar um método antigo, consultar os ADRs e o histórico para saber por que ele foi abandonado.

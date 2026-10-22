# AMORA — Arquitetura Técnica Canônica

**Documento:** arquitetura técnica oficial da Amora  
**Status:** CANÔNICO / ATUAL  
**Atualizado em:** 2026-10-22  
**Regra de autoridade:** este arquivo descreve a arquitetura vigente. Histórico, tentativas rejeitadas e decisões substituídas vivem em `docs/decisions/` e no histórico Git.

---

## 1. Invariante central

> **A Amora não é um modelo de linguagem.**

Modelos remotos são capacidade cognitiva/linguística substituível. A identidade, relações, memória, emoções, objetivos, intenções, privacidade e fatos canônicos da Amora pertencem ao Core.

Trocar provider ou platform adapter pode alterar fluência, criatividade, latência, UI e capacidade de integração, mas **não pode trocar quem a Amora é**.

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
    ├── Cloudflare Workers AI
    ├── OpenRouter (especialista / último recurso)
    ├── Gemini (tarefas específicas; não identidade)
    └── providers futuros
```

---

## 2. Plataforma

### 2.1 Host principal atual

Host doméstico atual: **Galaxy A15 5G**.

O Core continua local no Termux. A superfície primária de iteração conversacional é Safari/Web; Android debug permanece referência/gate secundário até existir o cliente Android oficial final.

```text
Galaxy A15 / Termux
└── Amora Core + estado canônico
    ├── Web/Safari adapter -> laboratório principal
    └── Android adapter    -> referência / integração nativa / gate secundário
```

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
- fila e verdade de entrega de `SpeechUtterance`;
- Voice Worker local Web;
- observabilidade, backup e restore.

### 2.3 Web/Safari — platform adapter

Responsável por:

- UI Web/PWA;
- pareamento/autenticação com o Core;
- captura de microfone via Web Audio;
- VAD/barge-in local;
- envio de WAV para o contrato de STT;
- exibição de texto;
- player Web Audio para WAV retornado pelo Core;
- interrupção manual/local;
- diagnóstico de latência/fila/voz.

O navegador **não** é dono de:

- identidade;
- memória;
- emoção;
- semântica social;
- fila canônica;
- VoiceSignature;
- escolha de provider;
- verdade persistente de entrega.

`window.speechSynthesis` não é o transporte principal da voz da Amora.

### 2.4 Android — Kotlin

Responsável, quando usado, por:

- microfone;
- captura e reprodução de áudio;
- foreground services;
- notificações;
- UI nativa;
- pareamento com o Core;
- integração Android;
- futura percepção de tela;
- futura hospedagem do Avatar Engine;
- engine/bridge de voz local no dispositivo.

O APK debug atual não define a arquitetura do cliente Android oficial final.

### 2.5 Identidades de plataforma

Pessoa, dispositivo e sessão são identidades diferentes.

```text
PERSON_BK != DEVICE_A15 != WEB_SAFARI != SESSION_X
```

Fila de áudio deve respeitar `device_id`; um cliente Web não reivindica automaticamente trabalho destinado ao Android e vice-versa.

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

### 11.2 Atos existentes

A V1 possui atos/semântica local para acknowledgement, self fact, disclosure, support request, answer question, continue topic, autonomous initiation, reflection e atos relacionais mais ricos como opinião, discordância, tease/complaint/boundary quando aplicável.

O plano carrega stance, relational attitude e closing mode quando necessário.

### 11.3 Validação pós-geração

Validação semântica estrutural não depende de blacklist de frases. Exemplos:

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

### 13.1 Malha atual

- **Groq:** provider cognitivo principal enquanto saudável;
- **Cloudflare Workers AI:** segundo eixo direto quando configurado e explicitamente confirmado zero-cost;
- **Gemini:** fallback/tarefas específicas quando configurado; não é identidade nem voz Web normal;
- **OpenRouter:** rota explicitamente gratuita como broker/especialista/último recurso;
- **fallback local:** mantém o Core funcional em degradação.

Nenhum adapter pode ativar billing silenciosamente.

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

## 14. Voz — identidade local compartilhada

### 14.1 Invariante

A voz é parte da continuidade percebida da Amora. Backend de TTS não é sozinho a identidade vocal.

```text
TtsBackend
↓
base/style aprovada
↓
VoiceSignature fixa
↓
Emotion / Prosody bounded
↓
PCM
```

Detalhes e parâmetros vivem em `docs/canon/VOICE_IDENTITY.md`, ADR-0010 e ADR-0011.

### 14.2 Identidade aprovada de laboratório

Backend candidato atual:

```text
sherpa-onnx-supertonic-3-tts-int8-2026-05-11
```

Style:

```text
60% speaker 0 + 40% speaker 4
```

Base e assinatura congeladas:

```text
AMORA_CHILD_FINAL_B_PLUS60
↓
AMORA_VOICE_SIGNATURE_V1_SOFT
```

Emotion/Prosody V2 pode apenas modular expressão dentro de bounds; não reabre timbre/base.

### 14.3 DSP compartilhado

A implementação aprovada de FINAL_B + VoiceSignature + expressão bounded vive em:

```text
android/app/src/main/cpp/amora_voice_processor.cpp
android/app/src/main/cpp/amora_voice_processor.h
```

Android chama essa implementação por JNI. O Voice Worker Termux compila o mesmo código como shared library e o carrega por C ABI/`ctypes`.

Não manter duas implementações divergentes do DSP.

### 14.4 Voice Worker Web/Safari

Pipeline:

```text
SpeechUtterance validada
↓
AmoraLocalVoiceRenderer / Termux
↓
Supertonic 3 + amora_voice.bin
↓
shared FINAL_B + Signature V1 Soft
↓
Emotion/Prosody V2 bounded
↓
WAV PCM16 mono
↓
Safari Web Audio
```

Regras:

- rota de áudio é autenticada;
- AUTH/SECRET não viram áudio;
- renderização não equivale a entrega;
- texto pode aparecer mesmo se áudio falhar;
- `started` ocorre quando playback começa;
- ACK falado só ocorre quando playback termina;
- barge-in/manual stop aborta fetch/player e cancela a utterance/turno;
- falha de voz libera a interface/fila;
- nenhuma falha troca silenciosamente para Gemini TTS ou Safari TTS.

### 14.5 Android atual

O caminho Android de produção existente pode continuar com Faber provisório enquanto a promoção nativa final não for concluída. O Android debug é referência/gate secundário; pequenas iterações de diálogo/Web não exigem novo APK.

### 14.6 Certificação

Identidade de laboratório aprovada não equivale a produto fisicamente certificado. Ainda medir voz, RTF/latência, barge-in, estabilidade, CPU/RAM/temperatura e equivalência percebida no hardware real.

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

O runtime deve expor saúde operacional como `ON`, `OFF`, `WAIT` e `DEGRADED` sem codificar integrações específicas no console.

---

## 17. Observabilidade

O console humano é uma interface operacional viva, não um dump infinito de logs.

Deve mostrar, quando disponível:

- estado atual (`IDLE`, `TRANSCRIBING`, `THINKING`, `RESPONDING`, `SPEAKING`, etc.);
- módulos/providers/plugins e saúde;
- provider/model em uso;
- quota/cooldown;
- sessão/plataforma ativa;
- estado de `WEB_SAFARI` separado de `DEVICE_A15` quando aplicável;
- estado do Voice Worker local e RTF;
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

## 20. Fechamento atual da V1

Estado sequencial atual:

1. **Voz local técnica:** sherpa-onnx/Piper pt-BR, pipeline local Android fisicamente comprovado — implementado.
2. **Identidade de diálogo + Emotion loop:** semântica local, validator, test isolation, addressee continuity — implementado.
3. **Malha de providers gratuitos:** Groq + Cloudflare + Gemini/OpenRouter guardados + fallback local — implementado.
4. **Voz candidata + certificação:** FINAL_B + Signature V1 Soft aprovadas em laboratório e integradas ao Web Voice Worker; gate físico Web/Safari e certificação final ainda pendentes.

ADR-0013 tornou Safari a superfície primária de iteração antes do fechamento físico completo porque o APK debug não deve dominar o ciclo de desenvolvimento.

---

## 21. Regras arquiteturais não negociáveis

1. Provider nunca é identidade.
2. Platform adapter nunca é identidade.
3. Estado canônico vive fora do LLM e fora do navegador.
4. Memória ambígua não vira fato automaticamente.
5. Speaker ID não é autenticação.
6. Emoção preserva causa/alvo quando aplicável.
7. Relações mudam lentamente.
8. Traits mudam ainda mais lentamente.
9. Silêncio é comportamento válido.
10. Não usar LLM quando algoritmo local resolve.
11. Não usar visão remota quando API/plugin/OCR local resolve.
12. Privacidade controla fluxo de dados, não personalidade.
13. Plugins são isolados.
14. Ações relevantes verificam resultado.
15. Providers/modelos novos são certificados.
16. Entidades e aliases são canônicos.
17. Perspectiva é resolvida antes de consolidar fatos quando possível.
18. Amora continua funcional em modo degradado.
19. Não depender de um free tier específico.
20. Tempo usa TimeService/Scheduler.
21. Pessoa, dispositivo e sessão são distintos.
22. Secrets nunca entram no contexto cognitivo/provider.
23. Disclosure e remote processing são decisões diferentes.
24. Event Bus é idempotente/deduplicável.
25. Persistência usa migrations e backup/restore verificável.
26. Personalidade não pode depender exclusivamente de prompt.
27. Provider realiza linguagem; Core detém semântica social sempre que possível.
28. Voz normal deve manter identidade vocal consistente e pt-BR.
29. Falha de provider ou quota não pode redefinir quem a Amora é.
30. `WEB_SAFARI` e `DEVICE_A15` permanecem destinos distintos.
31. Falha de áudio Web não pode bloquear o texto nem a interface inteira.
32. Safari `speechSynthesis` e Gemini TTS não são fallback silencioso do Voice Worker local.
33. FINAL_B/VoiceSignature congeladas não são retunadas para corrigir bug de transporte.
34. CI não equivale a certificação física.

---

## 22. Autoridade documental

- `docs/canon/ARCHITECTURE.md`: arquitetura atual.
- `docs/canon/GDD.md`: produto/comportamento atual.
- `docs/canon/SKILL.md`: regras para desenvolver a Amora.
- `docs/canon/VOICE_IDENTITY.md`: identidade e pipeline vocal atuais.
- `docs/roadmap/VERSIONS.md`: origem, destino e status de features por versão.
- `docs/decisions/`: por que decisões foram aceitas, rejeitadas ou substituídas.
- `PROJECT_STATE.md`: checkpoint operacional atual.
- documentos históricos e commits antigos não são especificação atual por padrão.

Antes de restaurar um método antigo, consultar os ADRs e o histórico para saber por que ele foi abandonado.

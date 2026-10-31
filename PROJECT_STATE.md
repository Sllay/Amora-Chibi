# PROJECT_STATE — Amora-Chibi

**Última atualização:** 2026-10-31  
**Base operacional:** `main` após PR #95 (`1c6ef0b0b7b651adf3c5f0c5eefe8b20439931bd`)  
**Objetivo:** checkpoint operacional curto; código/testes/CI atuais vencem descrições históricas antigas.

## 1. Autoridade

Ordem de leitura atual:

1. `docs/canon/SKILL.md`;
2. `docs/canon/ARCHITECTURE.md`;
3. `docs/canon/GDD.md`;
4. `docs/canon/DIALOGUE_EMOTION.md`;
5. `docs/canon/PROVIDER_MESH.md`;
6. `docs/canon/PEOPLE_IDENTITY.md`;
7. `docs/canon/PLATFORM_MESSAGING.md`;
8. `docs/canon/VOICE_IDENTITY.md`;
9. `docs/roadmap/VERSIONS.md`;
10. ADRs relevantes;
11. reviews de engenharia;
12. este snapshot;
13. código/testes/CI atuais.

Quando um documento antigo contradizer canon/ADR/código/testes mais recentes, não restaurar silenciosamente o método antigo.

## 2. Freeze operacional

**Congelado, não cancelado:**

- corpo/avatar visual final;
- rig/animação/expressão visual e integração cérebro→corpo;
- visual perception / screen mapping / OCR/keyframes;
- V1.2 estética/self-image/roupa/aparência.

**Liberado:** Web/PWA UI/UX, Pessoas/Família, Console, mensageria, adapters/workers, presença de plataforma, diagnóstico/configuração e voz não visual.

## 3. Persistência/runtime

- Core + Web/PWA: container Linux remoto.
- Storage: SQLite local ou libSQL remoto.
- schema atual: **13**.
- recovery auditado aceita snapshots 10/11/12/13 em destino remoto vazio e promove aditivamente até 13 sem editar o snapshot fonte.
- schema 13 adiciona `messaging_audit`, `messaging_outbound_queue` e `platform_presence`.
- `WEB_SAFARI != DEVICE_A15` permanece invariante.

## 4. Cognição, identidade e expressão — ATIVOS

Ativos/integrados:

- Scheduler + autonomia idle;
- autonomia → ActionEngine bounded;
- ACIR/Perspective + PerspectiveConsolidator;
- relationship learning corroborado;
- Memory / World / Self;
- Emotion Engine + Interaction Appraiser;
- DialoguePlan + RealizationFrame validator;
- provider mesh + Provider Protocol;
- STT Groq;
- Fish/Gemini Web voice;
- People Profiles + PlatformIdentityBinding + family authority;
- Web Chat / Pessoas / Console;
- observability/operations/security/container CI.

Provider não define `speaker_id`, família, memória ou identidade canônica. Literal conversation input não possui autoridade estrutural.

## 5. Passo 1 — CONCLUÍDO

PR #92 implementou:

- papel browser `operator` separado de `web` e do admin master;
- `amora pair --role operator` com challenge próprio;
- aba Pessoas/Família;
- CRUD de profiles;
- Relationship Graph familiar administrativo com histórico temporal;
- N bindings WhatsApp/Discord/Web por PERSON;
- IDs privados mascarados por padrão;
- export/import público separado de bindings privados;
- bootstrap JSON read-only como seed, storage canônico como runtime authority;
- Living Console seguro via `/v1/operations/state`.

ADR: `ADR-0028-operator-web-people-settings.md`.

## 6. Passo 2 — CONCLUÍDO EM CÓDIGO / STAGING DE REDE PENDENTE

PR #95 implementou a camada comum de mensageria.

```text
Discord / WhatsApp worker
        ↓
normalized InboundMessage
        ↓
PlatformIdentityBinding
        ↓
SocialAttentionRouter
+ bounded ConversationFloor
        ↓
Cognitive Core
        ↓
OutboundMessage queue
        ↓
worker / plataforma
```

Canon: `docs/canon/PLATFORM_MESSAGING.md`  
ADR: `ADR-0029-platform-messaging-social-attention.md`.

### 6.1 Identidade

- Discord user ID, WhatsApp JID/LID/telefone são **external actor identifiers**, nunca PERSON por si.
- Core resolve identidade via `PlatformIdentityBinding`.
- display name/nickname é apresentação contextual, não autenticação.
- desconhecido em grupo usa `EXT_*` efêmero; não cria `PERSON_*` automaticamente.

### 6.2 DM

```text
DM
→ resolve binding
   ├─ conhecida → cognition/reply permitido
   └─ desconhecida → QUARANTINE
                     → audit privado
                     → NÃO CognitiveStore/provider
```

DM desconhecida não ganha acesso cognitivo normal apenas por enviar texto.

### 6.3 Grupo / atenção

Evidências principais:

- native reply/quote para Amora: muito alta;
- native @mention da Amora: muito alta;
- vocativo explícito `Amora, ...`: alto;
- mesmo speaker dentro do floor ativo: continuidade bounded;
- ambiente sem atenção direta: context-only;
- menção explícita a outro ator / outro speaker tomando o floor: shift away.

Incerteza favorece silêncio.

`ConversationFloor` usa generation + reply lease curto. Se o grupo mudar enquanto o provider gera, a resposta antiga pode ser suprimida antes de entrar na fila outbound.

### 6.4 Audit != Memory

`messaging_audit` preserva histórico operacional/social suficiente para BK auditar manipulação e contexto:

- plataforma/account/conversation/message;
- direção;
- sender externo e PERSON resolvida quando houver;
- texto;
- reply/mention metadata;
- routing action/reason codes;
- se Amora respondeu.

Isso **não** consolida automaticamente todo grupo/DM em Memory ou Relationship Graph. Mensageria entra com `memory_consolidation=false` e `relationship_learning=false` até existir policy explícita futura.

### 6.5 Outbound

Core produz `OutboundMessage` normalizado e fila persistente. Worker faz claim/ack bounded.

Quando possível, resposta usa native reply/quote para a mensagem exata que chamou Amora, reduzindo ambiguidade em grupo.

### 6.6 Credencial de worker

Nova credencial:

```text
AMORA_MESSAGING_SERVICE_TOKEN
→ role messaging
→ somente GET/POST /v1/messaging/worker/**
```

Ela deve ser diferente do admin e do voice service token; em cloud exige no mínimo 32 bytes.

Worker comprometido não recebe Memory, World, Actions, People Settings, operator audit, Console ou `/v1/state`.

## 7. PlatformPresenceController — ATIVO

Core separa intenção de presença de transporte físico.

Persistido:

```text
desired_connected
check_requested
generation
```

Reportado pelo worker:

```text
DISCONNECTED
→ CONNECTING
→ CONNECTED
↔ CHECKING
→ DISCONNECTING
→ DISCONNECTED
```

Após restart/troca de compute, estado físico anterior volta para `DISCONNECTED` com `CORE_RESTART_TRANSPORT_UNKNOWN`, mas `desired_connected` sobrevive. Amora não finge que um socket antigo ainda existe.

`CHECK` permite connect/check/disconnect bounded quando a presença desejada permanente é false.

**Limite atual:** o controller e APIs estão integrados; o binding conversacional específico de linguagem natural como `Amora, conecta no Discord` pode ser adicionado sobre essa fundação sem alterar workers/contrato. Não alegar conexão física enquanto worker real não estiver provisionado.

## 8. Discord worker — IMPLEMENTADO / STAGING PENDENTE

Worker isolado em `workers/discord/`:

- Gateway v10 + REST v10;
- heartbeat / Identify / Ready / Resume / reconnect;
- stable Discord user ID;
- guild/DM;
- mentions;
- message reference/reply;
- attachment metadata;
- outbound native reply;
- `allowed_mentions.parse=[]` para evitar ping acidental gerado pelo modelo;
- mensagens de bots/self descartadas;
- presença controlada pelo Core.

Staging real ainda exige:

- bot/token real;
- intents/permissões corretas;
- `MESSAGE_CONTENT` quando aplicável;
- guild/canais de teste;
- prova física de reconnect/DM/grupo/reply.

Código implementado não equivale a bot já conectado.

## 9. WhatsApp linked-device worker — IMPLEMENTADO / NÃO-OFICIAL / STAGING PENDENTE

Worker isolado em `workers/whatsapp/` para a semântica desejada de uma conta/número normal como linked device.

Estado atual:

- dependência direta pinada em `@whiskeysockets/baileys@7.0.0-rc14`;
- CI instala o pacote, confirma versão, importa o worker real e executa audit HIGH;
- só `messages.upsert type=notify` vira input social;
- upsert com `requestId` é descartado como defesa adicional;
- full history sync desabilitado;
- auth state fica fora do Git/log/imagem;
- QR/pairing secret vai somente para auth dir privado;
- disconnect normal preserva sessão; não usa logout;
- quoted reply usa mensagem original em cache bounded quando disponível.

Baileys continua integração não-oficial em relação à Meta Cloud API e possui risco operacional/de conta.

`useMultiFileAuthState` está aceito somente para bootstrap/staging. Antes de deploy definitivo, mover auth state para backend durável/transacional e provar reconnect/offline sync com conta dedicada.

## 10. Gates do Passo 2

PR #95 foi mergeado somente após o mesmo SHA passar:

- Core Python 3.11;
- Core Python 3.12;
- Core Python 3.13;
- Core Python 3.14;
- Pre-deploy security;
- Cloud container contracts;
- regressões completas no container non-root/read-only;
- Messaging workers;
- import real do worker Baileys instalado;
- adapter contract tests;
- `npm audit --omit=dev --audit-level=high`.

Também há regressão dedicada de audit/outbound/presence em libSQL remoto com troca de compute home.

## 11. Voz Web / Background Voice

Web normal continua:

```text
WEB_SAFARI
→ Core
→ Fish Audio s2.1-pro-free + clone
→ PCM incremental
→ Safari
```

Background Voice enquanto Free Fire/outro app está foreground continua **NÃO IMPLEMENTADO / GATE FÍSICO PENDENTE**.

Direção aprovada:

```text
Safari Call Mode
↕ WebRTC audio
VoicePresenceBridge remoto
→ VAD → STT → Core → TTS
↕
iPhone com outro app em foreground
```

Não prometer always-on mic Web até spike físico passar.

## 12. Implementado mas ainda não vivendo completamente

- `SessionSpeakerTracker`: pronto, sem upstream real de diarização; WhatsApp/Discord não dependem dele.
- Core `EnergyVad`: dormente/legado enquanto Web usa VAD da superfície.
- Avatar Core/Studio: existe, mas corpo/avatar final segue congelado.

## 13. Próxima ordem recomendada

```text
PASSO 1 — CONCLUÍDO
✓ Web operator
✓ Pessoas/Família
✓ bindings privados
✓ import/export
✓ Living Console

PASSO 2 — CONCLUÍDO EM CÓDIGO
✓ PlatformMessagingContract
✓ identity gate
✓ unknown-DM quarantine antes da cognição
✓ SocialAttentionRouter
✓ bounded ConversationFloor
✓ audit/history separado de Memory
✓ outbound queue
✓ Discord worker
✓ WhatsApp linked-device worker isolado
✓ PlatformPresenceController
✓ scoped messaging service credential

STAGING PASSO 2 — quando credenciais/contas estiverem disponíveis
1. provisionar bot Discord e testar guild/DM/reply/reconnect
2. provisionar conta WhatsApp dedicada e parear linked device
3. substituir auth multi-file por storage produtivo
4. provar reconnect/offline sync sem replay indevido
5. opcional: ligar comandos conversacionais `conecta/desconecta/check` ao PresenceController

PASSO 3 — próximo bloco de engenharia
6. spike físico WebRTC Background Voice / Call Mode no iPhone + Free Fire
7. se PASS, integrar Call Mode ao Web normal
8. se FAIL, registrar limite Web; sem cliente nativo não prometer always-on mic

DÍVIDA
9. flatten da cognição guiado por replay
10. SpeakerTracker somente com diarização real
```

## 14. Últimos merges relevantes

- PR #87 — Provider authority/expression — `c7c329c49b37db517a08e858ff22c7d64290b8aa`.
- PR #88 — People Profiles + private bindings — `9c85d14be6f1d125869c058c4eb2725cf466deaa`.
- PR #89 — checkpoint identity/messaging direction — `e39760260783b47f4e48a2c5f948463f47168529`.
- PR #92 — Web People/Family Settings + operator + Console — `33b897def4a33708b0337bc96304628df89b8d24`.
- PR #93 — checkpoint Passo 1 — `a81a8acebf186cbf87961208f8001fecc2716d78`.
- PR #95 — Platform Messaging + social attention + audit + presence + Discord/WhatsApp workers — `1c6ef0b0b7b651adf3c5f0c5eefe8b20439931bd`.

Nenhum gate físico de Discord real, WhatsApp real, Background Voice/iPhone/Free Fire, visão ou corpo/avatar final é declarado PASS por inferência.

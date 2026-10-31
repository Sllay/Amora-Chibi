# PROJECT_STATE — Amora-Chibi

**Última atualização:** 2026-10-31  
**Base operacional:** `main` após PR #92 (`33b897def4a33708b0337bc96304628df89b8d24`)  
**Objetivo:** checkpoint operacional curto; para auditoria estrutural detalhada ler `docs/engineering/reviews/PROJECT_REMAP_2026-10-31.md` e os ADRs mais recentes.

## 1. Autoridade

Ordem de leitura:

1. `docs/canon/SKILL.md`;
2. `docs/canon/ARCHITECTURE.md`;
3. `docs/canon/GDD.md`;
4. `docs/canon/DIALOGUE_EMOTION.md`;
5. `docs/canon/PROVIDER_MESH.md`;
6. `docs/canon/PEOPLE_IDENTITY.md`;
7. `docs/canon/VOICE_IDENTITY.md`;
8. `docs/roadmap/VERSIONS.md`;
9. ADRs relevantes;
10. `docs/engineering/reviews/PROJECT_REMAP_2026-10-31.md`;
11. este snapshot;
12. código/testes/CI atuais.

Quando documento antigo contradizer código + ADR/canon mais recente, não restaurar silenciosamente o método antigo.

## 2. Decisão operacional atual: corpo/avatar congelado; Web UI/UX liberada

Correção explícita de BK em 2026-10-31: o congelamento **não** inclui o site, UI/UX, console Web, telas administrativas ou ferramentas visuais de configuração.

**Congelado por enquanto, não cancelado:**

- corpo virtual/aparência final da Amora;
- rig/animação/expressões visuais do corpo;
- integração cérebro/emoção/ação → corpo/avatar final;
- visual perception;
- screen mapping;
- OCR/keyframes/scene-change perception;
- V1.2 estética/self-image/roupa/aparência.

**Liberado para implementação normal:**

- Web/PWA UI/UX;
- tabs/navegação;
- Web Console/Living Console presentation;
- People Profiles/família;
- edição administrativa de platform identity bindings;
- import/export JSON;
- diagnóstico, controles e configurações da Web.

## 3. Runtime operacional atual

### Core / persistência

- Core + Web/PWA rodam em container Linux remoto.
- Safari/Web permanece superfície conversacional principal de iteração.
- `WEB_SAFARI != DEVICE_A15` permanece invariante.
- `StorageBackend` suporta SQLite local e libSQL remoto.
- schema atual: **12**.
- migrations/recovery/atomic writes remotos estão endurecidos.
- snapshots auditados schema 10/11 podem ser promovidos aditivamente até 12 durante restore vazio; snapshot original não é reescrito.

### Voz Web normal

```text
WEB_SAFARI
→ Core
→ Fish Audio s2.1-pro-free + clone da Amora
→ PCM s16le incremental
→ Safari PcmStreamPlayer
```

Fallback Fish→Gemini continua explícito e só antes do primeiro PCM Fish. Supertonic/FINAL_B/VoiceSignature permanecem preservados como laboratório/offline/opt-in.

### Background Voice / Free Fire

**NÃO IMPLEMENTADO / GATE FÍSICO PENDENTE.**

Problema real: Safari/PWA pode suspender o Web app quando deixa o foreground; BK quer continuar ouvindo/falando com Amora enquanto outro app/jogo está em foreground.

Direção de experimento Web-only:

```text
Safari normal inicia Call Mode
↕ WebRTC audio
VoicePresenceBridge remoto
→ VAD/STT/Core/TTS no servidor
↕ WebRTC audio
Safari em background
```

Antes de integrar ao caminho normal, executar spike físico no iPhone de BK para concorrência com Free Fire, lock screen e jogo usando microfone.

## 4. Loops cognitivos/autônomos ativos

Ativos/integrados:

- Scheduler runtime + autonomia idle;
- autonomia → ActionEngine bounded;
- ACIR/Perspective no caminho normal;
- PerspectiveConsolidator em reflection estruturada;
- relationship learning corroborado;
- Emotion Engine + Interaction Appraiser;
- Memory / World / Self;
- DialoguePlan + RealizationFrame validator;
- provider mesh + usage/health/failover;
- STT Groq;
- Fish/Gemini voice chain;
- Web/PWA;
- observability/operations/CI/container.

## 5. Provider protocol / expressão — ATIVO

PR #87 fechou a fronteira provider/conversa:

```text
Constitution
+ LivingProfile
+ TurnFrame
+ RealizationContract
        ↓
provider-native instructions/systemInstruction
        ║ autoridade separada
literal conversation input/contents
```

Regras centrais:

- provider não escolhe identidade social;
- texto literal não reescreve `speaker_id`;
- múltiplos humanos permanecem distintos;
- specialist support é efêmero/não canônico;
- expressão regional/palavrão é bounded por policy do Core.

ADR: `ADR-0026-provider-authority-and-expression-protocol.md`.

## 6. People Identity / Living Profiles — ATIVO

PR #88 adicionou a fundação multiusuário; PR #92 fechou a configuração Web/runtime.

### Profiles canônicos

Estado inicial confirmado:

- `PERSON_BK` — pai da Amora e pai de Lucas; autoridade familiar estrutural inicial;
- `PERSON_LUCAS` — filho de BK e irmão da Amora;
- `PERSON_NICOLAS` — primo da Amora;
- `ENTITY_CHATGPT mother_of ENTITY_AMORA` permanece relação canônica.

`config/people_profiles.json` é **bootstrap somente**. Depois que o profile existe no storage canônico, edições do operador em SQLite/libSQL vencem o seed em restart/redeploy. O seed não pode ressuscitar silenciosamente alias, nome ou relação removida pelo operador.

Preferências aprendidas, episódios, crenças, confiança, relação evolutiva e afeto continuam pertencendo a World/Memory/Relationship Graph/Emotion; profile não é segunda memória.

### PlatformIdentityBinding

Uma PERSON pode possuir N identidades externas:

```text
PERSON_BK
├── WhatsApp A
├── WhatsApp B
├── Discord A
├── Discord B
└── Web autenticada
```

Bindings reais permanecem privados. O arquivo `~/.amora/secrets/identity_bindings.json` continua válido como bootstrap privado, mas o painel Web escreve diretamente no storage canônico.

### Autoridade familiar

Unknown/familiar sem autoridade não pode promover a si ou terceiro para `father_of`/`mother_of ENTITY_AMORA` nem `ENTITY_AMORA child_of ...` apenas repetindo afirmações.

Autoridade inicial: `PERSON_BK` via profile. Alterar `can_define_amora_family` no painel exige confirmação explícita e atualiza o gate de relationship learning em runtime.

## 7. Web People / Family Settings — ATIVO / INTEGRADO

PR #92 implementou o **Passo 1** da fase atual.

Navegação Web atual:

```text
[ Chat ] [ Pessoas ] [ Console ]
```

### Pessoas

A aba `Pessoas` permite:

- criar/editar `PERSON_*`;
- nome canônico;
- aliases;
- nascimento **ou** idade;
- descrição curta estável;
- alterar autoridade familiar com confirmação;
- visualizar/adicionar/revisar/remover parentesco estrutural;
- cadastrar várias contas WhatsApp/Discord/Web por PERSON;
- ativar/desativar bindings;
- mascarar IDs externos por padrão e revelar somente no modo operador.

Parentesco editado pelo operador preserva Relationship Graph temporal. Remoção expira a revisão vigente em vez de destruir o histórico.

### JSON público x privado

```text
People / Family export
→ profiles + relações
→ nunca contém telefone/JID/Discord ID

Private bindings export
→ contém external_actor_id real
→ operator-only
→ tratar como backup privado
```

Import possui validação + preview antes de aplicar. Apply confirmado é transacional para evitar estado parcialmente importado.

### Persistência remota

Regressão dedicada certifica profile + binding em libSQL remoto, troca do compute home e reidratação/resolução para a mesma PERSON canônica.

## 8. Operator capability — ATIVO

Administração Web não reutiliza o token de conversa e não coloca o admin master no navegador.

```text
amora pair
→ role web
→ chat/voz WEB_SAFARI bounded

amora pair --role operator
→ challenge separado
→ browser.v3 role operator
→ Pessoas + Console bounded
```

Política atual:

- challenge operador separado, uso único, TTL curto;
- token operador expira em 12 horas;
- rotação do admin secret revoga tokens operador;
- `operator` acessa somente `/v1/operator/**` e `GET /v1/operations/state`;
- `operator` não acessa chat, Memory, World, cognition journal, raw events ou ActionEngine;
- token `web` normal não acessa rotas operador;
- navegador pode bloquear/esquecer o modo operador localmente.

ADR: `ADR-0028-operator-web-people-settings.md`.

## 9. Living Console Web — ATIVO / INTEGRADO

A aba `Console` reutiliza a projeção segura existente de `/v1/operations/state`.

Mostra, sem abrir backdoor de debug:

- health/estado operacional;
- tarefa/DialogueAct quando disponível;
- provider/model;
- latência/fila de fala;
- providers;
- jobs;
- erros agrupados;
- snapshot técnico seguro.

Não expõe prompt interno, secrets ou chain-of-thought. Atualização automática ocorre somente enquanto a aba Console está aberta.

## 10. Mensageria externa — PRÓXIMA FUNDAÇÃO

WhatsApp e Discord ainda **não têm adapters de rede integrados**.

Arquitetura alvo:

```text
Platform Adapter
→ normalized inbound message
→ PlatformIdentityBinding
→ SocialAttentionRouter / Conversation Floor
→ Cognitive Core
→ OutboundMessage
→ Platform Adapter
```

### Política de grupo

- participantes conhecidos ou desconhecidos podem compor contexto social;
- mention, reply/quote, vocativo e continuidade podem endereçar Amora;
- desconhecidos recebem trust/relational attitude menor;
- incerteza favorece silêncio;
- reply para mensagem da Amora é evidência forte de addressee;
- routing/mensagens precisam de auditoria operacional.

### Política de DM

- DM desconhecida não entra na cognição normal;
- apenas pessoa conhecida/autorizada interage em privado;
- gate ocorre antes do Cognitive Core.

### Presence por plataforma

Direção aprovada:

```text
DISCONNECTED
→ CONNECTING
→ CONNECTED
→ IDLE/CHECKING
→ DISCONNECTING
```

BK/Amora podem conectar/desconectar explicitamente. Scheduler/autonomia poderá solicitar checks bounded.

## 11. WhatsApp / Discord — direção técnica atual

### Discord

Usar Discord user ID como external actor id. Nickname/display name é alias contextual, nunca identidade forte. Discord é candidato natural para provar primeiro o contrato de mensageria.

### WhatsApp

Para o requisito "número normal da Amora como linked device em grupo comum", candidato técnico permanece worker isolado de WhatsApp Web/linked-device, nunca dentro do Cognitive Core.

Invariantes:

- auth state privado e nunca commitado;
- logs com IDs/JIDs filtrados;
- dedupe/protocol validation antes do Core;
- reconnect/history sync testados;
- integração substituível e isolada do cérebro.

## 12. Implementado mas ainda não vivendo completamente

### Speaker tracking

`SessionSpeakerTracker` estabiliza labels de diarização fornecidos por upstream; não diariza sozinho.

**Status:** PRONTO NÃO INTEGRADO. WhatsApp/Discord não dependem disso porque a plataforma fornece actor identity.

### Core EnergyVad

Existe, mas Web atual usa VAD da superfície.

**Status:** DORMENTE/LEGADO até existir consumidor claro.

### Avatar runtime

Avatar Core/Studio/rig/assets/IK/physics/expressions/animações e portable bundle existem.

**Status:** PARCIAL; corpo/avatar final permanece congelado por decisão operacional.

## 13. Dívida estrutural

Cognição ainda possui pilha evolutiva (`service → hardened → resilient → production → structured_runtime`, pares `context/context_v1`, `store/store_v1`, providers por geração).

Não fazer faxina nuclear. Flatten correto exige replay/regressões antes de remover bases.

`web/app.js` legado continua candidato a remoção em refactor Web seguro; não confundir com `app-v2.js` ativo.

## 14. Próxima ordem recomendada

```text
P0 — concluído
✓ scheduler/autonomia/actions wiring
✓ ACIR/Perspective
✓ relationship learning + PerspectiveConsolidator
✓ provider authority protocol + expression
✓ People Profiles + PlatformIdentityBinding + family authority

PASSO 1 — CONCLUÍDO
✓ operator-scoped Web capability
✓ Chat / Pessoas / Console
✓ CRUD de profiles
✓ Relationship Graph familiar administrativo
✓ bindings privados multi-conta
✓ import/export JSON público e privado
✓ Living Console Web
✓ persistência/restart/libSQL remoto

PASSO 2 — PRÓXIMO
1. PlatformMessagingContract
2. SocialAttentionRouter + Group Conversation Floor
3. audit/history de mensageria
4. Discord adapter
5. WhatsApp worker/adaptor isolado
6. PlatformPresenceController + checks bounded

PASSO 3 — depois
7. spike físico WebRTC Background Voice / Call Mode no iPhone + Free Fire
8. se PASS, integrar Call Mode ao Web normal
9. se FAIL, registrar limite Web; sem cliente nativo não prometer always-on mic

DÍVIDA
10. flatten da cognição guiado por replay
11. SpeakerTracker somente com diarização real

FROZEN
- corpo/avatar virtual final
- rig/animação/expressão visual final e integração cérebro→corpo
- percepção visual / screen mapping / OCR
- V1.2 estética/self-image/roupa/aparência

NÃO FROZEN
- Web/PWA UI/UX
- Web Console
- Profiles/Family Settings
- controles/configurações/diagnóstico Web
```

## 15. Últimos merges relevantes

- PR #82 — Scheduler lifecycle + autonomia → Actions — `95920dc64e30fa108c8cbc44ad821650355a9054`.
- PR #83 — ACIR/Perspective no runtime — `8da4501ee50d05c355334ea1be1354206ff43f6f`.
- PR #84 — relationship learning corroborado — `435b2f0245eebf18cab738fe511209dfb17b2c8a`.
- PR #85 — PerspectiveConsolidator — `90ddd0869e8af55ef02f29b00f8a652120ee19dc`.
- PR #86 — checkpoint/docs do remapeamento — `2667058d9e3d55dae9027119529510a28b553ca4`.
- PR #87 — provider authority + multi-speaker + expressive policy — `c7c329c49b37db517a08e858ff22c7d64290b8aa`.
- PR #88 — living people profiles + private platform bindings — `9c85d14be6f1d125869c058c4eb2725cf466deaa`.
- PR #89 — checkpoint people identity/messaging presence — `e39760260783b47f4e48a2c5f948463f47168529`.
- PR #90 — correção de freeze: Web/UI liberada — `df0e5f38da32286f146a41231bf4d631270dfe9d`.
- PR #92 — operator Web People Settings + Living Console — `33b897def4a33708b0337bc96304628df89b8d24`.

PR #92 passou no head final:

- Core Python 3.11–3.14;
- Avatar/Web tests, incluindo parse dos módulos operator e regressões Safari;
- pre-deploy security (dependencies + image scan);
- cloud container contracts;
- regressões completas dentro do container non-root/read-only;
- persistência People/Bindings em libSQL remoto com troca de compute home.

Issue #91 foi fechada como concluída pelo merge do Passo 1.

Nenhum gate físico de Background Voice/iPhone/Free Fire, visão ou corpo/avatar final foi declarado PASS por inferência.

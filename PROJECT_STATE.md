# PROJECT_STATE — Amora-Chibi

**Última atualização:** 2026-10-31  
**Base operacional:** `main` após PR #89 (`e39760260783b47f4e48a2c5f948463f47168529`)  
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

## 2. Decisão operacional atual: corpo/avatar visual congelado; Web UI/UX liberada

Correção explícita de BK em 2026-10-31: o congelamento **não** inclui o site, UI/UX, telas administrativas, console Web ou ferramentas visuais de configuração.

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
- navegação/tabs e superfícies administrativas;
- Web Console/Living Console presentation;
- tela visual de People Profiles/família;
- edição administrativa de platform identity bindings;
- import/export JSON;
- diagnóstico, controles e configurações do site.

`/v1/operations/state` continua sendo a fundação operacional segura para o futuro Web Console. Uma view administrativa deve ter papel/permissão próprio; pareamento Web conversacional não vira admin automaticamente.

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

Problema real: Safari/PWA pode suspender o Web app quando deixa o foreground; BK quer continuar ouvindo/falando com Amora enquanto outro app/jogo, como Free Fire, está em foreground.

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

PR #87 fechou a fronteira provider/conversa.

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

Regras:

- provider não escolhe identidade social;
- texto literal não consegue reescrever `speaker_id`;
- múltiplos humanos permanecem distintos;
- specialist support é efêmero/não canônico;
- Groq/OpenRouter usam Responses quando aplicável;
- Gemini usa `systemInstruction`;
- expressão regional/palavrão é bounded por policy do Core, não caricatura nem prompt solto.

ADR: `ADR-0026-provider-authority-and-expression-protocol.md`.

## 6. People Identity / Living Profiles — ATIVO

PR #88 adicionou a fundação necessária para WhatsApp/Discord multiusuário.

### Profiles seed

`config/people_profiles.json` contém bootstrap humano-editável pequeno. Não é segunda memória.

Estado inicial confirmado:

- `PERSON_BK` — pai da Amora e pai de Lucas; autoridade familiar estrutural inicial;
- `PERSON_LUCAS` — filho de BK e irmão da Amora;
- `PERSON_NICOLAS` — primo da Amora;
- `ENTITY_CHATGPT mother_of ENTITY_AMORA` permanece relação canônica pré-existente.

Idade/nascimento/descrição podem ser preenchidos. Preferências aprendidas, episódios, crenças, relação evolutiva e afeto continuam em World/Memory/Relationship Graph/Emotion.

### PlatformIdentityBinding

Uma PERSON pode possuir N contas externas:

```text
PERSON_BK
├── WhatsApp A
├── WhatsApp B
├── Discord A
├── Discord B
└── Web autenticada
```

Bindings reais são privados e não ficam em Git. O arquivo privado de bootstrap continua em `~/.amora/secrets/identity_bindings.json` ou `AMORA_IDENTITY_BINDINGS_FILE`.

### Autoridade familiar

Unknown/familiar sem autoridade não pode promover a si ou terceiro para `father_of`/`mother_of ENTITY_AMORA` nem `ENTITY_AMORA child_of ...` apenas repetindo afirmações.

Autoridade inicial: `PERSON_BK` via profile, não hardcode escondido.

### Web People / Family Settings — DIREÇÃO ACEITA

A Web pode ganhar uma aba administrativa visual para configurar pessoas, família e contas externas.

Princípio importante: o JSON versionado é **bootstrap**, não deve virar a fonte runtime mutável dentro do container cloud. O container empacota esse catálogo como arquivo read-only; alterações de produção devem persistir nos stores canônicos existentes.

Fluxo alvo:

```text
Web Settings / Pessoas
        ↓
operator-scoped API
        ↓
PersonProfileStore
RelationshipEvolutionService / Relationship Graph
PlatformIdentityBinding store
        ↓
persistência SQLite/libSQL
```

A UI deve oferecer:

- criar/editar pessoa canônica;
- nome, aliases, nascimento/idade e descrição curta;
- visualizar/editar relações familiares com histórico/provenance;
- configurar N contas WhatsApp/Discord/Web por PERSON;
- mascarar identificadores privados por padrão;
- importar JSON;
- exportar JSON/snapshot para edição/backup;
- validação antes de salvar;
- nunca gravar telefone/Discord ID no catálogo público/Git.

Para facilitar o uso, a UI pode apresentar visualmente a mesma estrutura dos JSONs, mas o **save de produção** vai para banco/store canônico. Export JSON reconstrói um documento portátil sem tornar o filesystem do container a autoridade.

ADR base: `ADR-0027-living-people-profiles-and-platform-bindings.md`.

## 7. Mensageria externa — PRÓXIMA FUNDAÇÃO

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

- participantes conhecidos ou desconhecidos podem compor contexto do grupo;
- mention, reply/quote, vocativo e continuidade social podem endereçar Amora;
- desconhecidos recebem trust/relational attitude menor;
- incerteza deve favorecer silêncio;
- reply para mensagem da Amora é forte evidência de addressee;
- mensagens e routing precisam de auditoria operacional.

### Política de DM

- DM desconhecida não entra na cognição normal;
- somente pessoa conhecida/autorizada pode interagir em privado;
- gate ocorre antes do Cognitive Core.

### Presence por plataforma

Desejo atual de produto:

```text
DISCONNECTED
→ CONNECTING
→ CONNECTED
→ IDLE/CHECKING
→ DISCONNECTING
```

BK/Amora podem conectar/desconectar explicitamente. Scheduler/autonomia poderá solicitar checks bounded.

## 8. WhatsApp — direção técnica atual

Para o requisito "número normal da Amora como linked device em grupo comum", candidato técnico é worker isolado usando cliente WhatsApp Web/linked-device, nunca dentro do Cognitive Core.

Se esse caminho for escolhido:

- release fixada;
- auth state tratado como credencial longa e nunca commitado;
- logs com JIDs/metadados filtrados;
- dedupe/protocol validation antes do Core;
- testar reconnect/history sync;
- reconhecer risco operacional de integração não oficial.

## 9. Discord — direção técnica atual

Adapter Discord deve usar **Discord user ID** como external actor id. Nickname/display name é alias contextual, nunca identidade forte.

Discord é candidato natural para provar primeiro o contrato de mensageria.

## 10. Implementado mas ainda não vivendo completamente

### Speaker tracking

`SessionSpeakerTracker` estabiliza labels de diarização fornecidos por upstream; ele não diariza sozinho.

**Status:** PRONTO NÃO INTEGRADO. WhatsApp/Discord não dependem disso porque a plataforma fornece actor identity.

### Core EnergyVad

Existe, mas Web atual usa VAD da superfície.

**Status:** DORMENTE/LEGADO até existir consumidor claro.

### Avatar runtime

Avatar Core/Studio/rig/assets/IK/physics/expressions/animações e portable bundle existem.

**Status:** PARCIAL; corpo/avatar final permanece congelado por decisão operacional.

## 11. Dívida estrutural

Cognição ainda possui pilha evolutiva (`service → hardened → resilient → production → structured_runtime`, pares `context/context_v1`, `store/store_v1`, providers por geração).

Não fazer faxina nuclear. Flatten correto exige replay/regressões antes de remover bases.

`web/app.js` legado segue candidato forte a remoção. Como Web UI/UX está liberada, essa limpeza pode entrar junto de refactors seguros da superfície Web.

## 12. Próxima ordem recomendada

```text
P0 — concluído
✓ scheduler/autonomia/actions wiring
✓ ACIR/Perspective
✓ relationship learning + PerspectiveConsolidator
✓ provider authority protocol + expression
✓ People Profiles + PlatformIdentityBinding + family authority

P1 — Web/operator UX
1. operator-scoped Web capability
2. aba Pessoas/Família
3. CRUD de profiles + relações + bindings privados
4. import/export JSON
5. Web Console usando /v1/operations/state

P1 — identidade/mensageria
6. PlatformMessagingContract
7. SocialAttentionRouter + Group Conversation Floor
8. audit/history de mensageria
9. Discord adapter
10. WhatsApp worker/adaptor isolado
11. PlatformPresenceController + checks bounded

P1 paralelo — voz fora do foreground
12. spike físico WebRTC Background Voice/Call Mode no iPhone + Free Fire
13. somente se PASS, integrar call mode ao Web normal

DÍVIDA
14. flatten da cognição guiado por replay
15. SpeakerTracker somente com diarização real

FROZEN
- corpo/avatar virtual final
- rig/animação/expressão visual final e integração cérebro→corpo
- percepção visual / screen mapping / OCR
- V1.2 estética/self-image/roupa/aparência

NÃO FROZEN
- Web/PWA UI/UX
- Web Console
- Profiles/Family Settings
- controles/configurações/diagnóstico da Web
```

## 13. Últimos merges relevantes

- PR #82 — Scheduler lifecycle + autonomia → Actions — `95920dc64e30fa108c8cbc44ad821650355a9054`.
- PR #83 — ACIR/Perspective no runtime — `8da4501ee50d05c355334ea1be1354206ff43f6f`.
- PR #84 — relationship learning corroborado — `435b2f0245eebf18cab738fe511209dfb17b2c8a`.
- PR #85 — PerspectiveConsolidator — `90ddd0869e8af55ef02f29b00f8a652120ee19dc`.
- PR #86 — checkpoint/docs do remapeamento — `2667058d9e3d55dae9027119529510a28b553ca4`.
- PR #87 — provider authority + multi-speaker + expressive policy — `c7c329c49b37db517a08e858ff22c7d64290b8aa`.
- PR #88 — living people profiles + private platform bindings — `9c85d14be6f1d125869c058c4eb2725cf466deaa`.
- PR #89 — checkpoint people identity/messaging presence — `e39760260783b47f4e48a2c5f948463f47168529`.

PR #88 passou Core Python 3.11–3.14, pre-deploy security e cloud container contracts, incluindo regressões completas no container non-root/read-only.

Nenhum gate físico de Background Voice/iPhone/Free Fire, visão ou corpo/avatar final foi declarado PASS por inferência.

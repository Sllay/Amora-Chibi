# Amora-Chibi

Monorepo oficial da **Amora**, uma IA-companheira persistente, mobile-first e multimodal.

> A Amora não é um LLM. Providers são recursos cognitivos substituíveis; identidade, memória, relações e estado persistente vivem no Core.

## Estado atual

**V0.8 — Natural Conversation / Turn Manager / Voice** em fechamento.

A base implementada até aqui inclui:

- V0.1: Event Bus idempotente, TimeService/Scheduler, identidade pessoa/device/session, SQLite WAL+migrations, Secrets Vault, API localhost, backup/restore e ModelRouter base;
- V0.2: Social Scene, Addressee, Perspective Resolver/Stack, ACIR, contratos VAD/Speaker/STT, Groq Whisper adapter e app Android nativo com áudio/VAD local e pareamento seguro;
- V0.3: Raw Event Log auditável + memória episódica/semântica/relacional, provenance, Memory Write Gate, retrieval híbrido local FTS5/BM25 + entidades/relações + RRF/MMR, soft forgetting e memória incluída em backup/restore;
- V0.4: World Model com observation/report/belief/inference/hypothesis, belief revision temporal, Canonical World State, Self Model protegido, relationship revisions e consolidação de perspectiva sobre ACIR resolvido;
- V0.5: Appraisal determinístico, Emotion Events com target/cause/blame/intentionality/spillover, Global Mood valence/arousal/dominance, Entity Affect por entidade, decay via TimeService e Social Bids candidatos com TTL;
- V0.6: Drives persistentes, Utility determinística, BDI, hysteresis/switch cost, silêncio explícito, contexto de interrupção, intenção persistente, decisão idempotente e idle scheduling via TimeService/Scheduler;
- V0.7: Skill Library, Planner hierárquico, Action Bus com gates de permission/privacy/risk/cost/idempotency/timeout, contratos de plugin, Execution Monitor, retry/replan/abort, persistência de planos/ToolCalls/runs e ações semânticas verificadas;
- V0.8: Turn Manager (`LISTEN/WAIT/BACKCHANNEL/TAKE_TURN/INTERRUPT/YIELD`), Fast Path local de fala/backchannels, fila TTS persistente com lease/ACK, integração idempotente Action→Conversation, privacy fail-closed para `AUTH/SECRET` e Android TextToSpeech com supressão do próprio áudio no VAD/STT.

Os documentos canônicos do projeto são `ARCHITECTURE.md`, `GDD.md` e `SKILL.md`, atualmente mantidos como arquivos anexados do Projeto ChatGPT. `AGENTS.md` contém os guardrails essenciais para agentes que trabalham neste repositório. Implementações por estágio ficam documentadas em `docs/`.

## Estrutura

```text
Amora-Chibi/
├── core/        # cérebro Python / Termux
├── android/     # app Android Kotlin
├── avatar/      # renderer web compartilhado
├── plugins/     # integrações modulares
├── tests/       # testes do Core
├── tools/       # scripts de desenvolvimento/Termux
└── docs/        # documentação complementar
```

## Termux + GitSync

O repositório pode ficar no armazenamento compartilhado para o GitSync, mas **não deve ser executado dali**. O Android não oferece ali todos os recursos de filesystem necessários para virtualenvs e executáveis Python.

Depois de sincronizar a `main` no GitSync, rode:

```bash
bash /storage/emulated/0/Documents/Amora-Chibi/tools/termux-bootstrap.sh \
  /storage/emulated/0/Documents/Amora-Chibi
```

O bootstrap:

1. copia o código para `~/amora-src` usando Python puro (sem `rsync`);
2. cria o virtualenv em `~/.venvs/amora`;
3. instala o wheel Android compatível de `pydantic-core` quando necessário;
4. instala a Amora e dependências;
5. roda `pytest`;
6. roda `amora doctor`.

Não rode `pip install -U pip` no Python global do Termux.

### Compatibilidade Python 3.14 / Android

O Termux atual pode fornecer Python 3.14. O PyPI não fornece wheel Android oficial de `pydantic-core`, então o projeto fixa temporariamente `pydantic==2.14.0b2` / `pydantic-core==2.49.0`, combinação para a qual existe wheel Android pré-compilado compatível com Termux Python 3.14.

O bootstrap obtém esse wheel do índice público:

```text
https://eutalix.github.io/android-pydantic-core/
```

Essa compatibilidade é isolada e deverá ser revisada quando houver wheel Android adequado para versões estáveis mais novas.

### Depois do bootstrap

```bash
source ~/.venvs/amora/bin/activate
cd ~/amora-src
amora doctor
amora backup
amora serve --port 8765
```

### Teste local da memória V0.3

```bash
amora memory remember "BK mostrou um desenho novo para Amora" \
  --entity PERSON_BK --entity ENTITY_AMORA --tag desenho

amora memory recall "desenho" --entity PERSON_BK
```

### Teste local do World Model V0.4

```bash
amora world record "BK não gosta de café" \
  --kind belief \
  --subject PERSON_BK \
  --predicate likes_coffee \
  --value false \
  --confidence 0.98

amora world state --subject PERSON_BK

amora world record "BK agora gosta de café" \
  --kind belief \
  --subject PERSON_BK \
  --predicate likes_coffee \
  --value true \
  --confidence 0.97

amora world state --subject PERSON_BK
amora world history --subject PERSON_BK --predicate likes_coffee
amora self show
```

### Teste local do Emotion Engine V0.5

```bash
amora emotion appraise "BK ignorou Amora de propósito" \
  --valence -0.9 \
  --target PERSON_BK \
  --agent PERSON_BK \
  --goal-relevance 0.9 \
  --novelty 0.4 \
  --expectedness 0.5 \
  --agency 0.95 \
  --control 0.6 \
  --intentionality 0.95 \
  --norm-violation 0.85 \
  --relationship-relevance 0.95 \
  --dedupe-key demo-bk-boundary

amora emotion state
amora emotion affect PERSON_BK
amora emotion affect ENTITY_CHATGPT
amora emotion events --target PERSON_BK
amora emotion bids
```

O esperado é `anger` dirigido a `PERSON_BK`, mood global parcialmente negativo, `anger` de `ENTITY_CHATGPT` permanecendo em zero e um Social Bid candidato como `set_boundary`. Repetir o mesmo appraisal com a mesma `--dedupe-key` não deve duplicar o impacto.

### Teste local da Autonomy V0.6

```bash
python -m amora.autonomy state

python -m amora.autonomy drive social 0.55 \
  --reason "longo período sem interação" \
  --dedupe-key demo-social-drive

python -m amora.autonomy cycle \
  --available PERSON_BK \
  --dedupe-key demo-autonomy-cycle

python -m amora.autonomy state
python -m amora.autonomy decisions
```

Repetir o mesmo `cycle` com a mesma `--dedupe-key` deve retornar a mesma decisão/intenção sem duplicar efeitos. Rodar um novo ciclo enquanto a mesma intenção continua melhor deve produzir `kept_current` por hysteresis.

### Teste local de Actions / Planner V0.7

Primeiro crie uma intenção ativa pela V0.6; depois:

```bash
python -m amora.actions state
python -m amora.actions plan --dedupe-key demo-v07-plan
python -m amora.actions execute --dedupe-key demo-v07-execute
python -m amora.actions plans
python -m amora.actions executions
```

Uma intenção social como `initiate_contact` deve usar a skill `core.semantic_intention`, gerar um ToolCall `core.actions.emit_intention`, publicar `action.semantic_request`, verificar o efeito e então marcar a intenção como concluída. Repetir `execute` com a mesma dedupe key deve devolver o run anterior sem executar a tool novamente.

Ações não idempotentes, remotas, de risco acima do permitido ou sem permission são bloqueadas pelo Action Bus. Conteúdo `AUTH` nunca entra na execução de tools.

### Teste local de Conversation / Voice V0.8

Sem depender de STT ou provider remoto, a fila de voz pode ser validada pelo Termux:

```bash
python -m amora.conversation state
python -m amora.conversation say "Papis?" --dedupe-key demo-v08-first-voice
python -m amora.conversation utterances
```

Com o Core em `127.0.0.1:8765` e o APK V0.8 pareado/rodando, o Android reivindica essa utterance, fala via `TextToSpeech` e envia ACK ao Core. Depois:

```bash
python -m amora.conversation state
python -m amora.conversation utterances
```

O esperado é `speech_pending_count=0`, `speech_spoken_count=1` e a utterance em `spoken`.

Uma ação autônoma V0.6/V0.7 `initiate_contact -> PERSON_BK` também deve enfileirar uma única fala `Papis?`. Replay da mesma ação não deve duplicar a fala.

A conversa pelo microfone continua dependendo de um STT configurado. Captar áudio não significa responder: o Turn Manager arbitra speaker/addressee/floor/pausas/urgência. Durante o TTS, o Android drena o microfone mas não alimenta VAD/STT com a própria voz da Amora.

Runtime padrão:

```text
~/.amora/
├── data/amora.db
├── backups/
├── cache/
├── logs/amora.jsonl
└── secrets/
```

O token da API local é criado automaticamente em `~/.amora/secrets/LOCAL_API_TOKEN` e **nunca deve ser commitado**.

A API do Core escuta somente `127.0.0.1` por padrão.

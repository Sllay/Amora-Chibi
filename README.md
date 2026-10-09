# Amora-Chibi

Monorepo oficial da **Amora**, uma IA-companheira persistente, mobile-first e multimodal.

> A Amora não é um LLM. Providers são recursos cognitivos substituíveis; identidade, memória, relações e estado persistente vivem no Core.

## Estado atual

**V0.9 — Avatar Studio + Runtime** em fechamento.

A base implementada até aqui inclui:

- V0.1: Event Bus idempotente, TimeService/Scheduler, identidade pessoa/device/session, SQLite WAL+migrations, Secrets Vault, API localhost, backup/restore e ModelRouter base;
- V0.2: Social Scene, Addressee, Perspective Resolver/Stack, ACIR, contratos VAD/Speaker/STT, Groq Whisper adapter e app Android nativo com áudio/VAD local e pareamento seguro;
- V0.3: Raw Event Log auditável + memória episódica/semântica/relacional, provenance, Memory Write Gate, retrieval híbrido local FTS5/BM25 + entidades/relações + RRF/MMR, soft forgetting e memória incluída em backup/restore;
- V0.4: World Model com observation/report/belief/inference/hypothesis, belief revision temporal, Canonical World State, Self Model protegido, relationship revisions e consolidação de perspectiva sobre ACIR resolvido;
- V0.5: Appraisal determinístico, Emotion Events com target/cause/blame/intentionality/spillover, Global Mood valence/arousal/dominance, Entity Affect por entidade, decay via TimeService e Social Bids candidatos com TTL;
- V0.6: Drives persistentes, Utility determinística, BDI, hysteresis/switch cost, silêncio explícito, contexto de interrupção, intenção persistente, decisão idempotente e idle scheduling via TimeService/Scheduler;
- V0.7: Skill Library, Planner hierárquico, Action Bus com gates de permission/privacy/risk/cost/idempotency/timeout, contratos de plugin, Execution Monitor, retry/replan/abort, persistência de planos/ToolCalls/runs e ações semânticas verificadas;
- V0.8: Turn Manager (`LISTEN/WAIT/BACKCHANNEL/TAKE_TURN/INTERRUPT/YIELD`), Fast Path local de fala/backchannels, fila TTS persistente com lease/ACK, Action→Conversation idempotente, privacy fail-closed para `AUTH/SECRET` e Android TextToSpeech com supressão do próprio áudio no VAD/STT;
- V0.9: Avatar Studio/Runtime web compartilhado, edição visual touch de parts/layers/pivôs, rig humanoide padrão, 2-bone IK, color slots, expressões, mouth flap, blink, physics secundária, timeline, PWA, persistência schema 9, assets separados e Mind→Avatar semântico.

Os documentos canônicos do projeto são `ARCHITECTURE.md`, `GDD.md` e `SKILL.md`, mantidos como arquivos anexados do Projeto ChatGPT. `AGENTS.md` contém guardrails essenciais para agentes. Implementações por estágio ficam em `docs/`.

## Estrutura

```text
Amora-Chibi/
├── core/        # cérebro Python / Termux
├── android/     # app Android Kotlin
├── avatar/      # Avatar Studio + Runtime web
├── plugins/     # integrações modulares
├── tests/       # testes do Core
├── tools/       # scripts de desenvolvimento/Termux
└── docs/        # documentação complementar
```

## Termux + GitSync

O repositório pode ficar no armazenamento compartilhado para o GitSync, mas **não deve ser executado dali**. Execute da cópia privada do Termux.

Primeira instalação/bootstrap:

```bash
bash /storage/emulated/0/Documents/Amora-Chibi/tools/termux-bootstrap.sh \
  /storage/emulated/0/Documents/Amora-Chibi
```

Atualizações já instaladas podem usar o sync do projeto:

```bash
bash /storage/emulated/0/Documents/Amora-Chibi/tools/termux-sync.sh \
  /storage/emulated/0/Documents/Amora-Chibi
```

Depois:

```bash
source ~/.venvs/amora/bin/activate
cd ~/amora-src
amora doctor
```

Não rode `pip install -U pip` no Python global do Termux.

### Compatibilidade Python 3.14 / Android

O Termux atual pode fornecer Python 3.14. O projeto fixa temporariamente `pydantic==2.14.0b2` / `pydantic-core==2.49.0`, combinação para a qual existe wheel Android/arm64 compatível com Termux Python 3.14.

O bootstrap obtém esse wheel de:

```text
https://eutalix.github.io/android-pydantic-core/
```

Essa compatibilidade deverá ser revisada quando versões estáveis novas tiverem wheel Android adequado.

## Core local

```bash
source ~/.venvs/amora/bin/activate
cd ~/amora-src

amora doctor
amora backup
amora serve --port 8765
```

A API escuta somente `127.0.0.1` por padrão.

## Teste local da memória V0.3

```bash
amora memory remember "BK mostrou um desenho novo para Amora" \
  --entity PERSON_BK --entity ENTITY_AMORA --tag desenho

amora memory recall "desenho" --entity PERSON_BK
```

## Teste local do World Model V0.4

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

## Teste local do Emotion Engine V0.5

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

O esperado é `anger` dirigido a `PERSON_BK`, mood global parcialmente negativo, isolamento do affect de outras entidades e um Social Bid candidato apropriado. Repetir a mesma dedupe key não duplica impacto.

## Teste local da Autonomy V0.6

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

Replay da mesma dedupe key devolve a mesma decisão/intenção. Um novo ciclo com a mesma intenção ainda dominante deve usar hysteresis e mantê-la.

## Teste local de Actions / Planner V0.7

Primeiro crie uma intenção ativa pela V0.6; depois:

```bash
python -m amora.actions state
python -m amora.actions plan --dedupe-key demo-v07-plan
python -m amora.actions execute --dedupe-key demo-v07-execute
python -m amora.actions plans
python -m amora.actions executions
```

Uma `initiate_contact` deve usar `core.semantic_intention`, gerar `core.actions.emit_intention`, publicar `action.semantic_request`, verificar o efeito e concluir a intenção. Replay não repete a tool.

## Teste local de Conversation / Voice V0.8

Sem STT ou provider remoto:

```bash
python -m amora.conversation state
python -m amora.conversation say "Papis?" --dedupe-key demo-v08-first-voice
python -m amora.conversation utterances
```

Com o APK pareado e o Core em `127.0.0.1:8765`, o Android reivindica a utterance, fala via `TextToSpeech` e envia ACK. Uma ação autônoma `initiate_contact -> PERSON_BK` também pode gerar `Papis?` sem texto fornecido pelo usuário.

## Avatar Studio + Runtime V0.9

Diagnóstico:

```bash
python -m amora.avatar state
python -m amora.avatar project
python -m amora.avatar assets
python -m amora.avatar url
```

Com o Core rodando:

```bash
amora serve --port 8765
```

Abra no Chrome do A15:

```text
Studio:  http://127.0.0.1:8765/avatar/
Runtime: http://127.0.0.1:8765/avatar/?mode=runtime
```

Se necessário:

```bash
amora pair
```

O Studio foi desenhado mobile-first. Rig, pivôs, IK, layers, color slots, expressões, physics e timeline são manipulados visualmente. O JSON é somente serialização automática.

Fluxo recomendado:

```text
Gerar rig humanoide
→ importar sprites
→ ajustar layers/pivôs/bones
→ criar IK padrão
→ configurar cores/face
→ physics secundária
→ timeline
→ salvar
→ Runtime
```

Detalhes: `docs/V0.9_AVATAR.md` e `avatar/README.md`.

## Backup V0.9

A partir da V0.9, um backup inclui o snapshot SQLite e um sidecar para os binários do avatar:

```bash
amora backup --name checkpoint.db
```

```text
~/.amora/backups/checkpoint.db
~/.amora/backups/checkpoint.db.avatar.zip
```

O sidecar é validado por tamanho/SHA-256 contra os metadados do próprio snapshot SQLite. Um asset requerido ausente ou corrompido faz o backup falhar.

## Runtime padrão

```text
~/.amora/
├── data/amora.db
├── avatar/
│   └── assets/
├── backups/
├── cache/
├── logs/amora.jsonl
└── secrets/
```

O token local fica em:

```text
~/.amora/secrets/LOCAL_API_TOKEN
```

e **nunca deve ser commitado**.

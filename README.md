# Amora-Chibi

Monorepo oficial da **Amora**, uma IA-companheira persistente, mobile-first e multimodal.

> A Amora não é um LLM. Providers são recursos cognitivos substituíveis; identidade, memória, relações e estado persistente vivem no Core.

## Estado atual

**V0.3 — Memory** em fechamento.

A base implementada até aqui inclui:

- V0.1: Event Bus idempotente, TimeService/Scheduler, identidade pessoa/device/session, SQLite WAL+migrations, Secrets Vault, API localhost, backup/restore e ModelRouter base;
- V0.2: Social Scene, Addressee, Perspective Resolver/Stack, ACIR, contratos VAD/Speaker/STT, Groq Whisper adapter e app Android nativo com áudio/VAD local e pareamento seguro;
- V0.3: Raw Event Log auditável + memória episódica/semântica/relacional, provenance, Memory Write Gate, retrieval híbrido local FTS5/BM25 + entidades/relações + RRF/MMR, soft forgetting e memória incluída em backup/restore.

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

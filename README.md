# Amora-Chibi

Monorepo oficial da **Amora**, uma IA-companheira persistente, mobile-first e multimodal.

> A Amora não é um LLM. Providers são recursos cognitivos substituíveis; identidade, memória, relações e estado persistente vivem no Core.

## Estado atual

**V0.1 — Core Foundations**.

A fundação atual inclui:

- Event Bus idempotente com deduplicação persistente no SQLite;
- TimeService/Scheduler com relógio simulado;
- Entity Registry + Relationship Graph persistentes;
- Device/Session Identity;
- Secrets Vault local fora do Git;
- SQLite WAL + migrations + backup/restore;
- API local autenticada (`127.0.0.1`);
- ModelRouter desacoplado de providers;
- logging estruturado;
- testes de restart/recovery.

Documentos canônicos na raiz:

- `ARCHITECTURE.md`
- `GDD.md`
- `SKILL.md`

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

Use o GitSync para manter o repositório em uma pasta compartilhada e copie o código para o filesystem privado do Termux antes de executar:

```bash
pkg install python git rsync
bash tools/termux-sync.sh /storage/emulated/0/Documents/Amora-Chibi
cd "$HOME/amora-src"
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e '.[dev]'
pytest
amora doctor
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

## Comandos

```bash
amora doctor
amora backup
amora serve --port 8765
```

A API V0.1 escuta somente `127.0.0.1`.

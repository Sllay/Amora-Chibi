# Amora-Chibi

Monorepo oficial da **Amora**, uma IA-companheira persistente, mobile-first e multimodal.

> A Amora não é um LLM. Providers são recursos cognitivos substituíveis; identidade, memória, relações e estado persistente vivem no Core.

## Estado atual

**V0.1 — Core Foundations** em desenvolvimento.

Escopo inicial:

- Event Bus idempotente;
- TimeService/Scheduler com relógio simulado;
- Entity Registry e Relationship Graph mínimos;
- SQLite com migrations;
- runtime separado do repositório;
- interfaces iniciais do ModelRouter;
- testes automatizados.

Os documentos canônicos são:

- `ARCHITECTURE.md` — arquitetura técnica;
- `GDD.md` — produto e comportamento;
- `SKILL.md` — regras de continuidade do desenvolvimento.

## Estrutura

```text
Amora-Chibi/
├── core/        # cérebro Python / Termux
├── android/     # app Android Kotlin (futuro estágio V0.8+)
├── avatar/      # renderer web compartilhado (V0.9)
├── plugins/     # integrações modulares
├── tests/       # testes do Core
├── tools/       # scripts de desenvolvimento/Termux
└── docs/        # documentação complementar
```

## Termux

A recomendação é **não executar o projeto diretamente no armazenamento compartilhado do Android**. Use o GitSync para sincronizar o repositório e copie o código para o armazenamento privado do Termux:

```bash
pkg install python git rsync
bash tools/termux-sync.sh /storage/emulated/0/Documents/Amora-Chibi
cd "$HOME/amora-src"
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e '.[dev]'
pytest
python -m amora doctor
```

O runtime fica por padrão em:

```text
~/.amora/
├── data/
├── backups/
├── cache/
├── logs/
└── secrets/
```

Nada dessa pasta deve ser versionado no Git.

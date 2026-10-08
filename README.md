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

Os documentos canônicos do projeto são `ARCHITECTURE.md`, `GDD.md` e `SKILL.md`, atualmente mantidos como arquivos anexados do Projeto ChatGPT. `AGENTS.md` contém os guardrails essenciais para agentes que trabalham neste repositório, e `docs/V0.1_CORE.md` documenta a fundação implementada aqui.

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

Depois de sincronizar a `main` no GitSync, rode apenas:

```bash
bash /storage/emulated/0/Documents/Amora-Chibi/tools/termux-bootstrap.sh \
  /storage/emulated/0/Documents/Amora-Chibi
```

O bootstrap:

1. copia o código para `~/amora-src` usando Python puro (sem `rsync`);
2. cria o virtualenv em `~/.venvs/amora`;
3. instala o wheel Android compatível de `pydantic-core` para evitar compilação Rust no telefone;
4. instala a Amora e dependências;
5. roda `pytest`;
6. roda `amora doctor`.

Não rode `pip install -U pip` no Python global do Termux.

### Compatibilidade Python 3.14 / Android

O Termux atual pode fornecer Python 3.14. O PyPI não fornece wheel Android oficial de `pydantic-core`, então a V0.1 fixa temporariamente `pydantic==2.14.0b2` / `pydantic-core==2.49.0`, combinação para a qual existe wheel Android pré-compilado compatível com Termux Python 3.14.

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

A API V0.1 escuta somente `127.0.0.1`.

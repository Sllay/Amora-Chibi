# Aiven Phase 1 — offline audit of certified PCS.4C backup

Status: isolated engineering candidate. **Do not merge/deploy until PR CI passes.**
Related issue: #268. This is not a PostgreSQL adapter, data import, or schema migration.

## Operator facts

- Aiven PostgreSQL 18 preflight returned `status=ok`, `read_only=true`, `tls=verify-full` and exit 0 on 2026-11-17.
- Operator confirmed the existing PCS.4C persistent volume is still attached to the manual Job at `/private`.
- PCS.4C recovery code writes the snapshot to `/private/pcs4c/backup/amora-pre-pcs4c.db`, accompanying `.avatar.zip`, and certification state to `/private/pcs4c/state.json`.
- Historic certified report: schema 18, 51 logical tables, 48,059 rows, 0 assets, backup/verify/restore/restart OK. The live retained files have **not** been reaudited as part of this PR.

## Why this tool exists

Calling `python -m amora.storage.pcs4_recovery verify ...` directly emits SHA-256 hashes into logs. For operator-friendly reports, the new `aiven_snapshot_audit` module reuses exactly the existing `verify_snapshot` logic, but prints **only sanitized aggregates** and predefined failure codes.

It:
1. Refuses missing snapshot/state or an incomplete/active PCS.4C certification.
2. Runs the PCS.4C SQLite integrity, FK, sidecar, asset-manifest, data/hash and schema 18 validation in read-only mode.
3. Compares source and sidecar hashes **privately** with the hashes pinned in `state.json`, and validates stored backup/verify/restore counts.
4. Counts structural SQL objects without reporting table names, schema definitions or data, and records database/archive byte sizes.
5. Checks that `state.json` did not change while reading. Never creates, uploads, imports, deletes, or updates database contents. No PostgreSQL or Turso connection is created.

It emits an `AIVEN_SNAPSHOT_AUDIT` JSON line with `status=ok` or `status=blocked`; hashes, secret strings, paths and record content are excluded.

## Operator execution (only after CI green and approved image is available)

Use the **existing** PCS.4C Manual Job and its persistent volume; do not create a new paid service or volume. The Northflank UI lets an operator [preview and override configuration for an individual manual run](https://northflank.com/docs/v1/application/run/run-an-image-once-or-on-a-schedule). In the run preview, use an **execution-scoped command override** (not a permanent job CMD edit) when available:

```text
python -m amora.storage.aiven_snapshot_audit
```

The one-off job **must** run a Core image that includes this module and mounts the same persistent volume at `/private`. Preserve the PCS.4C job's normal configuration, original CMD and state. Never use the isolated `deploy/Dockerfile.aiven-preflight` image: it deliberately contains only the Aiven connectivity checker and does **not** contain the snapshot auditor.

Do not run if the user interface does not provide an execution-scoped override, or if enabling this command would alter the normal job permanently. Confirm resources are covered by the existing zero-cost allowance before an execution.

Successful output shape:

```json
{"status":"ok","phase":"aiven-phase1-offline","read_only":true,"schema_version":18,"tables":51,"rows":48059,"certification_hashes_match":true,"postgres_import_performed":false}
```

Actual output includes other aggregate counters and sizes; the values above are historical expectations, **not evidence of a new run**. If blocked, report only `reason`. Do not publish file contents, hash values, raw stack traces or connection strings.

## What a successful audit does *not* prove

- The snapshot contains the latest data. Any changes after backup capture, while Turso was accessible, remain outside it.
- PostgreSQL can execute the SQLite/FTS5 schema unchanged. It cannot.
- The data plus indexes will fit the free Aiven 1 GB allocation. That needs capacity engineering.
- Runtime PostgreSQL compatibility, privacy, rank semantics or an operational rollback.

Next engineering gate: map the SQL stores, design the PostgreSQL schema and full-text/JSON equivalence and run synthetic compatibility tests without live memory data. Preserve v18 source and keep `AMORA_STORAGE_BACKEND` unchanged until a separately certified cutover.

## Operador — evidência pós-CI, 2026-11-17 (commit da imagem auditada: `35dbf5bf`)

Job `pcs4c-recovery` com mesmo volume `/private` retornou `AIVEN_SNAPSHOT_AUDIT` com `status=ok`, exit code `0`, `read_only=true` e `certification_hashes_match=true`. Métricas sanitizadas:

| Campo | Resultado |
|---|---:|
| Schema | v18 |
| Tabelas lógicas | 51 |
| Registros de todas as tabelas | 48.059 |
| Arquivo SQLite | 75.563.008 bytes (72,0625 MiB) |
| Sidecar ZIP | 151 bytes |
| Assets | 0 |
| Índices | 65 |
| Tabelas virtuais FTS5 | 1 |
| Triggers / views | 0 / 0 |

Essas 48.059 linhas **não são contagem de memórias individuais**; incluem outras tabelas e estruturas de histórico. A certificação garante apenas equivalência à **cópia preservada**, não atualização em relação a um Turso atualmente bloqueado. O comando não migrou nem alterou runtime/dados de produção.

## Matriz preliminar de compatibilidade: Core v18 → PostgreSQL 18

Base: leitura direta do código de `main` no commit `75372e1`; este inventário não é conversão implementada nem teste de semântica real. Os contratos completos devem ser medidos em CI antes de qualquer importação de dados privados.

| Dependência existente | Evidência no código | Direção de engenharia e gate obrigatório |
|---|---|---|
| Conexão/transações | `core/amora/storage/backend.py` expõe `SQLConnection`; `database.py` usa `executescript`, `BEGIN IMMEDIATE` e ledger de migração | Introduzir backend explícito com transações verdadeiras, rollback e erro de commit ambíguo; sem mudar selector de produção nem substituir SQL genericamente |
| Schema e FKs | `migrations.py` e `migrations_v*.py`; tipos SQLite `TEXT`, `INTEGER`, `REAL`, `BLOB`, `AUTOINCREMENT`, `ON CONFLICT` | Esquema PostgreSQL versionado por migration, preservando IDs, PK, FK/CASCADE, CHECK, UNIQUE, DEFAULT e ordem de importação; decisões de tipo definidas por coluna, não por busca textual |
| Busca de memória | `memory/store.py` usa `CREATE VIRTUAL TABLE ... fts5`, tokenizer `unicode61 remove_diacritics 2`, `MATCH`, `bm25`, RRF e MMR | Avaliar `tsvector/tsquery` com índice GIN e normalização; **ts_rank NÃO equivale automaticamente ao bm25**. Congelar fixture de ranking e privacidade, comparar top-K, tie-break e escopo antes do corte |
| JSON/arrays | `memory/store.py` inclui `json_extract`, `json_each`, `json_array_length`, colunas `*_json` | Semântica de `jsonb`, extração e array expansion deve preservar tipos, nulos, igualdade, ordenação e limites de privacidade; testar acesso e invalidação de memórias |
| Filtragem lexical | `memory/store.py` usa `instr(lower(...), ...)`, comparação de datas como strings ISO, concatenação `||` | Definir operações PostgreSQL explícitas, Unicode/acentos e comportamento de NULL; não assumir ordenação/timestamp equivalente |
| Identidades e política de escopo | `memory/store.py` consulta ACL/owner/audience/remote gate; `database.py` e outras stores persistem identidade e histórico | Testes de isolamento por pessoa, privada/pública, arquivada, exclusão/edição, escopo remoto, dedupe e restart sem fallback para um banco vazio |
| Assets e backup | `pcs4_recovery.verify_snapshot` valida ZIP e hash por asset; volume v18 tem `assets=0` | Regras de `bytea`/blobs e restore precisam funcionar mesmo com assets em fixtures sintéticas, não apenas porque o backup atual não tem assets |
| Conexões concorrentes | `core/amora/storage/backend.py` gerencia instâncias por store; `memory/store.py` mantém conexão com lock próprio | Aiven Free tem `max_connections=20`, com até seis conexões reservadas: orçar o conjunto Core + Discord + jobs sem contar com pooler do serviço gratuito; medir em lab |

Referências oficiais para projeto de tipos e busca:
- https://www.postgresql.org/docs/18/textsearch-controls.html
- https://www.postgresql.org/docs/18/functions-textsearch.html
- https://www.postgresql.org/docs/18/functions-json.html
- https://aiven.io/docs/products/postgresql/concepts/pg-free-tier
- https://aiven.io/docs/products/postgresql/reference/pg-connection-limits

**Gates seguintes**: (i) esquema SQL PostgreSQL completo auditado, (ii) testes com fixture sintética para constraints/transações/FTS/JSON/rank/privacidade/rollback, (iii) carga de cópia do snapshot em destino isolado aprovado pelo operador, (iv) comparação por tabela + restart real, (v) reconciliação das diferenças temporais após último backup; só então uma mudança de backend em produção com rollback.

# Aiven Phase 2F.1 — audit inventory of serving Core SQL

Status: **read-only SQL audit**, a release gate for a future production PostgreSQL backend. Does not migrate a single live query, create PG tables, inspect backup bytes or connect to Aiven/Turso.

## Why this checkpoint exists

Previous PRs #269–#283 developed independent PostgreSQL 18 lab contracts for schema, import, memory ACL/FTS/BM25 and atomic updates, rows and transactions, conversation/identity domains, voice leases and cancellation. **These lab functions are not wired into the actual Core stores.** The serving `AmoraDatabase`, `MemoryStore`, `ConversationStore` and other modules still execute SQLite dialect with `?`, JSON1, FTS5, `PRAGMA` and SQLite-specific transaction/cursor assumptions. Safely switching the `AMORA_STORAGE_BACKEND` string would be incorrect.

The repository also contains schema v19 migration code. The retained PCS.4C snapshot was certified as v18 (51 tables / 48,059 rows). Source snapshot version, current runtime schema and future PG target schema are **different facts** and must be reconciled explicitly; the presence of schema v19 modules is not evidence that the certified snapshot has been updated.

## What is implemented

`core/amora/storage/postgres_runtime_sql_audit.py` uses Python's AST to traverse all `core/amora/**/*.py` serving modules, excluding the standalone `postgres_*` experimental code. It detects `execute`, `executemany`, `executescript`, and `execute_atomic_statement` calls:

- Static SQL text (including simple file-level constants and concatenations) is flagged for SQLite JSON1, FTS5/BM25, SQLite catalog/sequence, `PRAGMA`, conflict/transaction patterns and `?` placeholders.
- Unresolved dynamic SQL is classified as **unreviewed**, never silently marked compatible.
- Statements with no known marker are marked `unreviewed_sql`, **not portable or PG-certified**. This is a conservative scanner, not a complete SQL parser; some calls may be unrelated `execute` methods, and some SQL construction may be outside its detection scope.
- Deterministic report contains source filename, line, operation name, flags and a short SQL fingerprint. **Raw SQL and parameters are not printed**; no secrets, backup contents or user memory are accessed.
- Explicit `require_postgres_serving_ready` still fails with `CORE_POSTGRES_SQL_DIALECT_NOT_CERTIFIED`. Existing `backend_from_environment` continues accepting only sqlite/libsql.

`tests/test_postgres_runtime_sql_audit.py` checks real Core store coverage, JSON1/FTS5/qmark detection, dynamic SQL fail-closed, fingerprint redaction, invalid-source rejection and nonactivation of PostgreSQL in the serving backend.

## CI improvement

`.github/workflows/aiven-postgres-lab.yml` now runs these tests alongside the real **disposable PG18** integration suite on stacked Aiven PRs.

`.github/workflows/core-tests.yml` now accepts PR base branches matching `feat/aiven-**` as well as `main`. This fixes the prior gap where stacked Aiven PRs could show 4/4 workflows green **without running the Python 3.11–3.14 Core matrix**. Actual CI results must be checked; merely updating the trigger is not itself a green certification.

## Next release-critical engineering steps

1. Use the audited source sites to build a deterministic **query contract registry** with reviewed PostgreSQL SQL and domain-level authorization tests, starting with `AmoraDatabase`, `MemoryStore`, `ConversationStore`, then other stores. Do not apply generic `?`→`%s` string substitutions: placeholders can appear in string literals and JSON/FTS semantics differ.
2. Implement a single versioned PG backend that enforces atomic memory+posting writes, strict typed errors, coherent transactions and explicit leases. Integration tests must boot actual Core services against PG, not just call lab helpers.
3. Reconcile the certified SQLite v18 source snapshot with any v19 schema/runtime changes, concurrent writes since backup, remaining tables/assets, PG storage budget and operator-approved rollback.
4. Run Core tests + PG18 tests, restart/restore, privacy/identity/voice E2E, and load/capacity gates. Only then consider a real Aiven migration rehearsal and production switch.

No `main` merge, Northflank redeploy, Aiven schema extension, Turso write or backup import is part of this PR.

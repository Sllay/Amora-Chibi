# Aiven Phase 2A — PostgreSQL 18 synthetic memory laboratory

Status: isolated candidate, not a production storage backend. This change DOES NOT import or inspect any real user memory.

## Certified prerequisite

The 2026-11-17T16:02Z operator Job verified the retained v18 PCS.4C snapshot and sidecar by the pinned SHA values in private state: 51 logical tables, 48,059 rows across **all** tables, 65 indexes, one FTS5 table, 0 assets, 75,563,008 byte SQLite file, 151 byte ZIP. \`status=ok\`; exit code 0. The current source (Turso) remains inaccessible due to quota; this is a point-in-time snapshot, not proof of freshness.

## This PR implements

- A **deliberately restricted laboratory** \`core/amora/storage/postgres_memory_lab.py\` containing reviewed PostgreSQL 18 DDL for the v18 \`events\` schema and four core memory tables: \`memory_records\`, \`memory_entities\`, \`memory_tags\`, \`memory_links\`, plus a PostgreSQL-specific \`memory_search\` index table. It preserves source memory column names, constraints, foreign keys, serialized JSON and ISO timestamp strings for the synthetic domain.
- GIN index on \`to_tsvector('simple', content || ' ' || tags)\` as an experiment for candidate retrieval. This is **not** FTS5 \`unicode61\` + \`bm25\` ranking parity, nor is \`ts_rank\` a drop-in replacement for the hybrid RRF/MMR ranker.
- Fixed helper functions to install lab schema, write synthetic sample memories transactionally and query candidates with owner and remote/secret/archive filters applied BEFORE the ranking cap.
- Strict pre-DDL guard: only loopback connections to a database named \`amora_pg_synthetic\` running PostgreSQL major version 18. Production Aiven URLs, non-local hosts, other database names and mismatched versions fail closed before any SQL. No \`AMORA_POSTGRES_DSN\` parsing or secret handling.
- Unit tests for safety guards without PostgreSQL, plus a separate GitHub Actions workflow running a disposable \`postgres:18-alpine\` service and synthetic CI-only records, covering integrity constraints, FK cascade, uniqueness, rollback, JSON extraction, GIN presence, owner/privacy isolation, remote-only and archived filters. No calls to Aiven, Turso, Northflank or real data.
- No changes to \`AMORA_STORAGE_BACKEND\`, production Core bootstrap, Dockerfile, default library dependencies, memory authority, Discord integrations or v18/v19 snapshot migration.

## What this laboratory does not implement

- It is **not** the complete 51-table PostgreSQL schema and not yet the serving \`SQLConnection\` adapter. The event and memory domain slice is sufficient to demonstrate real PostgreSQL semantics, not to import a full snapshot.
- The synthetic input function is NOT a general-purpose production import/export path. It intentionally has no snapshot file I/O.
- Full-text ranking parity, exact Unicode/accent behavior, entity/relationship RRF/MMR parity, complete JSON queries, concurrent leases, boot/restart, schema migrations, complete privacy policy and backup/rollback are **not** certified here. A production migration must fail closed until separate parity suites pass.
- A pass on CI PostgreSQL does not prove memory data freshness or that all 51 tables + indexes fit the Aiven free 1 GB disk quota.
- No user needs to create any external database or run a Northflank job for this phase; the job runs only ephemeral local PostgreSQL in GitHub CI.

## Remaining design work (Phase 2B onward)

1. Complete a **column-by-column PostgreSQL v18 schema** for every logical table with explicit data-type, constraint, FK and index maps; cross-check generated metadata with a synthetic SQLite Core snapshot.
2. Define a bounded/secure logical exporter preserving privacy, exact source version, cross-table counts, PK/FK integrity, SQL assets and deterministic digest in private storage only.
3. Build an explicit PostgreSQL backend and dialect per query family (FTS, SQLite JSON1, row-shapes, placeholders, transactions, busy/ambiguous commits). Avoid blind regex replacement and silent fallback.
4. Certify canonical memory rankings and owner/recipient scopes with adversarial synthetic fixtures before the real copy.
5. Import the **verified copy** in isolated destination only when complete schema and capacity budgets pass and the operator approves; compare all 51 tables and resume/restart behavior.
6. Reconcile updates newer than the historical snapshot before any production cutover, then promote only after explicit rollback validation.

## References

- Source of truth: \`docs/engineering/reviews/AIVEN_POSTGRES_PHASE0.md\`, \`AIVEN_POSTGRES_PHASE1.md\`, \`core/amora/storage/migrations.py\`, \`core/amora/memory/store.py\`
- PostgreSQL 18 text search: https://www.postgresql.org/docs/18/textsearch-tables.html
- Aiven Free: https://aiven.io/docs/products/postgresql/concepts/pg-free-tier

Operational rule: Do not run \`install_lab\` on Aiven. It rejects such connections; CI fixture database \`amora_pg_synthetic\` is the only supported target.

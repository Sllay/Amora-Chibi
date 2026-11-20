# Aiven — private v18 staging import (development, NOT cutover)

## What is implemented

The offline PCS.4C snapshot verifier and PostgreSQL structural preflight already exist in the development branch. This phase adds amora.storage.postgres_private_staging_import.import_private_staging_v18 as a **caller-injected staging importer**. It intentionally has **no CLI, no DSN handling, no environment selector, no automatic scheduled job and no production switch**.

The caller must provide:
1. A private immutable SQLite v18 PCS.4C snapshot and .avatar.zip sidecar, on a controlled filesystem; the source verifier checks structure, integrity, foreign keys and media manifest.
2. A separately approved, already connected psycopg3 PostgreSQL 18 connection with autocommit true, target pinned by PostgresTargetPolicy, TLS verify-full for remote, and the **canonical pre-installed empty amora_pg_v18_lab schema** including BM25 postings, speech stops and the projection epoch.
3. Explicit confirmation string IMPORT_VERIFIED_V18_TO_EMPTY_ISOLATED_STAGING, and an **isolated destination designated for staging**. A name/schema alone cannot certify operational isolation; the operator must check it independently.

The importer validates structure and target emptiness *before writes*. All relational rows and FTS5 logical documents are copied in a single PG transaction with deferrable FK validation. Every source table is compared using typed row multisets; identity sequence high-water values are updated only after the full copy. FTS5 per-document token counts and postings are generated from an **in-memory copy** of SQLite, keeping the private original read-only. A mismatch raises a sanitized code and fails closed. All output consists of aggregate counts, not memory text, token strings, row IDs, connection strings or raw hashes.

**Caution:** Postgres sequence setval is not transactional; on import failure or uncertain COMMIT state, discard the entire isolated destination. Never retry into a potentially partial database. The importer's success confirms only staging rows/parity, not source freshness or production integrity.

## Scope and current blockers

- CI tests use fabricated local backups on disposable localhost PG18. No real memory, provider key, Northflank token or Aiven connection is used in CI.
- This PR does **not provision** an Aiven schema or execute a private data transfer; target DDL preparation and permissions require an independently reviewed, operator-authorized release gate.
- A real import must be run from a private Job that has a COPY of the certified backup, secret-mounted Aiven credentials/CA, a separate external recovery backup, and no Core/Discord workers actively writing to the destination.
- The historical PCS.4C 48,059-row count is **not** a proof of current source freshness; freeze/reconciliation against Turso must be completed before final cutover.
- Before production, additionally certify real Aiven capacity and 20-connection budget, encrypted off-instance pg_dump and independent pg_restore, exact privacy and BM25 ranking, startup/restart/rollback, and full Discord/provider/voice flow.
- The root Dockerfile still does **not** install the PG driver. AMORA_STORAGE_BACKEND still only accepts sqlite|libsql, so **no runtime switch is implemented or authorized** by this PR.

## Safety contract

The module does not open any external connection, read credentials, alter Core config, create PG schemas, change main, or configure any Northflank job. Only a caller holding a verified connection and the literal operator approval can invoke staging writes. A failed gate does not fall back to Turso/SQLite or any synthetic importer. Production remains blocked until the full release checklist in AIVEN_PRODUCTION_RECOVERY_AND_CUTOVER_GATE.md is completed.

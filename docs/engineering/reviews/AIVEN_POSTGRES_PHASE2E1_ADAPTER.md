# Aiven Phase 2E.1 — PostgreSQL 18 SQLConnection contract lab

Status: **isolated, explicit subset**, not a production PostgreSQL adapter.

## Starting evidence

The Phase 2D.5 PR #278 HEAD `ebb49c24` finished all five workflows green, including PostgreSQL 18 synthetic index updates, Core tests for Python 3.11–3.14, cloud container, pre-deploy security and perceptual memory. The historical PCS.4C backup (51 SQLite logical tables, 48,059 aggregate rows, schema v18) remains verified but untouched.

## Inspected Core contract

`core/amora/storage/backend.py` defines a synchronous SQLConnection with `execute`, `executemany`, `executescript`, `close`, context-managed transactions, `QueryResult.rowcount`, `fetchone`, `fetchall` and iteration. Core stores commonly use both `row[0]` and `row["id"]`, `dict(row)`, `execute_atomic_statement`, and nested DB accesses. The serving Core still uses SQLite/libSQL dialect, including `?` placeholders, `sqlite_schema`, `PRAGMA`, FTS5, JSON1 and `BEGIN IMMEDIATE`—these **are not legal PostgreSQL queries**.

## Implemented in this checkpoint

New module `core/amora/storage/postgres_sql_connection_lab.py`:
- Wraps a **caller-supplied** PostgreSQL 18 psycopg connection that is already connected with autocommit to loopback `amora_pg_synthetic`. `ensure_local_lab` runs at wrapper construction and before each operation. No DSN, network connector, production backend selector or local snapshot access.
- Supports a **small, fixed allowlist** of explicitly reviewed PostgreSQL SQL statements using psycopg `%s` positional parameters (count, owner-restricted retrieval, insert-only memory, revision CAS update, tags). Unknown statements and all raw SQLite SQL fail closed; no regex rewriting of queries or hidden compatibility fallback.
- Exposes `LabRow` supporting integer and column-name indexes and mapping conversion; materialized `LabResult` retains stable rows after transaction commit, exposes `rowcount`, `fetchone`, `fetchall` and iteration. Results above 1,000 rows are explicitly rejected; the approved query set is bounded.
- `with connection:` creates a real PostgreSQL transaction, nested contexts use psycopg savepoints, exceptions cause rollback. Batch writes are only allowed inside a transaction and capped. A failed COMMIT/ROLLBACK marks the wrapper unusable and raises an ambiguous-outcome code; **never replay a potentially committed write**.
- `execute_atomic_statement` can use the wrapper directly with its own laboratory capabilities metadata; **`backend_from_environment` stays unchanged** and `AMORA_STORAGE_BACKEND=postgres` is rejected.

Real disposable PostgreSQL 18 CI tests cover:
- Row positional/name mapping, `dict(row)`, query result shape, persistence across commits and a freshly opened connection.
- Atomic single statements, revision-checked updates, failed duplicate batches and nested savepoint rollback.
- Unknown SQLite/DDL/raw SQL and injected parameters rejected, remote Aiven connections rejected before SQL.
- Public selector remains sqlite/libsql only, no production integration.

## NOT yet implemented / blockers

This is **not** a complete `StorageBackend` for the Core. It does not implement `connect(path)`, migrations, backup, all SQLite query families or the private production data path. It may not be activated by environment variables. Future steps must implement explicit PostgreSQL SQL for events, identity registry, conversations, memory stores, cognitive runtime, leases, actions, jobs, assets, migrations v19 and all 51-table domains, with exhaustive SQLite-vs-PG behavioral comparison and error mapping.

A future production backend must additionally validate:
1. Full privacy and recipient ACL on every query, and exact ranking/Unicode behavior.
2. Concurrent writes/leases, transaction isolation, connection pool sizing within Aiven Free limit, ambiguous commit recovery, read-after-write and process restart.
3. Complete schema and source freshness reconciliation, operator-approved isolated import of the PCS.4C copy, all rows/IDs/assets, backup restore and rollback tests.
4. Capacity and performance with real saved records before any `AMORA_STORAGE_BACKEND` switch.

**Do not merge/deploy as a storage switch. No Aiven/Turso/PCS.4C traffic.**

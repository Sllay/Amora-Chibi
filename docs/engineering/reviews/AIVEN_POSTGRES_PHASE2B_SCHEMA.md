# Aiven Phase 2B.1 — canonical v18 schema inventory

Status: isolated code+CI candidate. Source: canonical SQLite migrations in repository, not private snapshot content.

## Certified starting point
- Aiven readonly TLS preflight successful; no import.
- PCS.4C volume /private retained. Operator certified schema v18, 51 logical tables, 65 SQL indexes, 1 FTS5 virtual table, no triggers/views, 48,059 aggregate rows, 75,563,008 SQLite bytes and matching certification hashes.
- Phase 2A synthetic PostgreSQL 18 memory laboratory green in dedicated GitHub Actions.

## Implemented this checkpoint

The new module core/amora/storage/postgres_v18_manifest.py builds a deterministic schema-only manifest from canonical migrations 1–18, then SQLite PRAGMA table_xinfo, foreign_key_list, index_list, index_xinfo and sqlite_schema introspection.

It inventories:
- Column names, declared SQLite types, whitelisted proposed PostgreSQL scalar types, required/default flags and primary key ordinals.
- Foreign key parent and ordered columns, update/delete actions; UNIQUE constraints including composites.
- Original canonical CREATE TABLE/INDEX definitions to review CHECK/default semantics (these SQLite statements are never sent to PostgreSQL).
- Special sqlite_sequence source table as source autoincrement metadata, not a PostgreSQL user table.
- Separate FTS5 memory_fts virtual table; shadow tables excluded from logical inventory, PG search needs explicit independent DDL and rank-parity certification.

Invariants are 51 source logical tables = 49 relational + 1 FTS5 + 1 sqlite_sequence, 65 explicit indexes, no triggers or views. Unknown source data type, expression UNIQUE, migration version drift or mismatched counts fails closed.

## Still required before PostgreSQL import

This is NOT complete PostgreSQL DDL and not a PostgreSQL backend. SQLite CHECK semantics, AUTOINCREMENT, collations, BLOB length, JSON and FTS5 are not safely translated by string substitution.

Next:
1. Author reviewed DDL for all 49 PostgreSQL relational tables and required search structures with type, FK, CHECK, UNIQUE, index and identity parity.
2. Compare PostgreSQL schema introspection against the synthetic SQLite manifest with explicit exceptions for FTS5/shadow and sqlite_sequence.
3. Run synthetic privacy/ranking, restarts/rollback and concurrent lease tests; budget Aiven disk and connection limits.
4. Only then request a separate, explicit operator approval to import a copy into an isolated database. Keep 48,059 rows labeled as snapshot historical aggregate; no freshness claim.

All this work is offline in CI, no source snapshot, Turso, Aiven, Core or Northflank production configuration touched.

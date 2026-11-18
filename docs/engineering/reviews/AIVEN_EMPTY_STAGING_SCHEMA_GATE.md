# Aiven PostgreSQL 18 — reviewed empty staging schema gate

**State: code candidate. NO LIVE AIVEN DDL OR DATA IMPORT HAS BEEN RUN.**

## Purpose and design

The previous PR adds an operator-injected importer for a verified PCS.4C v18 backup, but deliberately requires an *already prepared empty staging schema*. This step implements that schema preparation, using the existing reviewed v18 migration manifest and the **same** BM25/voice/memory-epoch DDL used by disposable PostgreSQL 18 tests.

This code adds no Northflank Job entrypoint, no DSN parser, no secrets, no Core backend selector, no deploy and no automatic invocation. It cannot discover or connect to Aiven: a separately reviewed operator-only runner must pass a psycopg connection and an exact PostgresTargetPolicy. A failed external job must not be retried against the same uncertain destination.

## Safety gates before any write

- Exact literal caller confirmation PREPARE_EMPTY_PG18_ISOLATED_STAGING_ONLY.
- Exact schema amora_pg_v18_lab; no accepting arbitrary schema input, no unreviewed SQL translator.
- Connection must match pinned host, port, dbname and PostgreSQL major 18. For remote endpoints, host must be *.aivencloud.com and TLS sslmode=verify-full with verifiable CA as required by PostgresTargetPolicy.
- Connection must support autocommit and explicit transaction context. The importer is **not** a publicly callable API endpoint.
- PostgreSQL metadata checks require that the staging schema **does not already exist** and the database currently contains **zero** non-system tables. If other application objects exist, return a sanitized block; don't delete/replace/truncate anything.
- Recheck these conditions inside one bounded transaction (5s lock and 60s statement timeout). Schema, all ordinary tables/indexes/foreign keys/CHECKs, FTS text search and postings, speech stops, and the monotonic memory projection epoch/triggers are installed atomically.
- Post-commit read-only structural preflight must return **preimport_structural_pass**. The canonical schema_migrations table is still EMPTY: normal serving preflight must remain **blocked**, and AMORA_STORAGE_BACKEND must not switch.

## Test evidence (CI only; to be certified)

The new tests use a disposable local PostgreSQL 18 database with no secret data:
1. Create a freshly prepared empty schema with expected 51-source/49-relational table manifest and canonical indexes.
2. Verify PG18 preimport structural state but serving blocked.
3. Reject missing confirmation and existing schemas without any new DDL.
4. Reject any unrelated pre-existing user table, wrong/remote target policy and TLS mismatch.
5. Import a **fabricated**, offline v18 SQLite + sidecar into the prepared schema using the previously validated staging importer; verify typed row parity and full structural preflight after import.

No real Aiven backup, credentials, operator secrets, private content or Northflank resource is used in GitHub Actions.

## Before a real staging schema may be created

- Complete the **live readonly Aiven inventory** from PR #294. Independently verify the dashboard still says Free, measure database bytes, understand 1 GB storage shared with WAL/temp/FTS indexes, and confirm the target is suitable for staging.
- Confirm the PCS.4C private pair's integrity *and freshness* relative to any Turso writes after its snapshot.
- Create and verify a distinct encrypted external backup and a tested rollback process; use a least-privilege staging-only DB role.
- Explicitly approve a manual staging Job with the appropriate image and fixed target, no schedule/CD/automatic retries and a copy of the private volume, not the live Core. Do not mutate the old pcs4c-recovery state/volume.
- Even with staging import success, production remains blocked pending independent restore, principal/owner data parity, search ranking/BM25 and full Discord/voice/Core tests.

**This PR deliberately supplies no executable script that could provision the live Aiven instance just by changing a Job command.** It adds the versioned internal function and disposable tests first.

Aiven Free reference: https://aiven.io/docs/products/postgresql/concepts/pg-free-tier

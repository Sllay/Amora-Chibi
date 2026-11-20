# Aiven PostgreSQL — Run 1: Core persistence infrastructure

**Implementation scope:** code on `feat/aiven-full-runtime-integration`. This is **not** production release approval. End-to-end correction and validation remain separate work by operator request.

## Implemented infrastructure

- `PostgresRuntimeConnection` exposes the synchronous `SQLConnection` read, write, row mapping, iterators, `executemany`, and transaction contract expected by Core stores. PostgreSQL uses native psycopg3 cursor binding, not qmark replacement.
- Queries are allowed only through an explicit one-to-one `ReviewedStatement` registry. All other SQL, arbitrary scripts, and unsupported operations fail closed. Each approved statement includes parameter count and result cap; batches require an explicit transaction.
- psycopg3 transaction contexts (including nested savepoints) protect multi-statement writes. Ambiguous COMMIT/ROLLBACK failures poison and close the connection. There is no write replay or failover to another database.
- Each logical connection uses a reentrant lock for its whole transaction scope, preventing another thread from interleaving operations within that transaction.
- `PostgresTargetPolicy` pins host, port, database, schema and PostgreSQL major. Remote connections require `sslmode=verify-full` and an explicit CA file; effective libpq connection metadata and TLS are checked before statements.
- `make_psycopg_connection_factory` accepts explicit runtime credentials; it is lazy, masks connection error details, and does not read env vars or create a connection until invoked.
- `PostgresBackendCandidate` implements the `StorageBackend` `connect(path)` interface with a bounded (maximum 16) connection budget and deterministic lease release on connection close/failure. It is *injected* by an embedding application and is **not** selectable through `AMORA_STORAGE_BACKEND`.
- `AmoraDatabase.migrate()` detects the candidate and skips all SQLite migration DDL. The candidate requires an *already prepared and imported* PostgreSQL v18 schema, checks the canonical relational table/column inventory and migration sequence 1..18, and does not create or alter PostgreSQL tables.
- `AmoraDatabase.schema_version()` has an explicit schema-qualified reviewed metadata mapping in the candidate.
- `PostgresBackendCandidate.backup()` intentionally fails closed: SQLite file copying is not a PostgreSQL consistency guarantee. Consistent PostgreSQL export, recovery and production rollback need their own reviewed implementation and gates.

## Explicit deferred work (Run 2 / Run 3)

These items are not evidence of a Run 1 infrastructure defect. They are necessary before any real cutover:

1. Register **all** domain SQL statements with reviewed PostgreSQL equivalents; remove SQLite-only SQL from serving paths (memory, FTS5/BM25, identity, conversation, voice, actions, autonomy, messaging, etc.).
2. Build and review the canonical destination schema and data import process for the **real** Aiven database, reconcile v18/v19 and source freshness, and confirm all privacy/identity invariants. The current v18 synthetic `amora_pg_v18_lab` is disposable, not production.
3. Integrate memory posting maintenance and exact retrieval/ranking behavior, transactional voice leases, principal-level authorization and cross-store lifecycle loops.
4. Add PostgreSQL-native consistent snapshots, operator-approved rollback and safe cutover controls. No backend flag, credential or production service is switched by Run 1.
5. Execute real PG18 and Python 3.11–3.14 CI suites, fix issues and perform full functional diagnostics after coding stages as separately authorized.

## Constraints and diagnostics

- No Aiven instance, production credential, Turso, PCS.4C private snapshot, Northflank deployment or production configuration was accessed or mutated.
- The synthetic schema guard is structural; a populated table or a green schema check is **not** proof of correct data retention, search ranking, or application behavior.
- The candidate is deliberately isolated from the default runtime selector. An incomplete SQL registry cannot silently fall back to SQLite or libSQL.
- Failures return stable error codes and never print SQL parameters or connection secrets from the candidate implementation.
- Tests authored for the Run 1 contract: `tests/test_postgres_runtime_connection.py`. Its CI path is registered in `.github/workflows/aiven-postgres-lab.yml`. **No CI result is certified by this document.**

# Aiven PostgreSQL — Run 3: ancillary SQL integrations and diagnostic ledger

**Repository:** `Sllay/Amora-Chibi`
**Branch:** `feat/aiven-full-runtime-integration`
**State:** Run 3 coding and static audit **recorded**, but NOT migration certification.
**Scope:** code and diagnostic inventory only. The owner reserved correction, functional testing and deployment decisions for the stage after the three runs.

## Run 3 code changes

1. `postgres_runtime_generation.py` + `messaging/generation_lifecycle.py`:
   reviewed PG18 CAS for ingress turn generation claim, release, staged outbound
   recovery and invalidation. Original SQLite code remains selected unless the
   explicitly injected backend has `capabilities.name == "postgres-candidate"`.
2. `postgres_runtime_reflections.py`:
   reviewed PG18 SQL for the original `ReflectionQueue` (trusted enqueue,
   crash-gap reconciliation, invalidation, source-scoped memory archiving,
   bounded re-claim, retry/defer, metrics). JSON1 expressions are replaced with
   concrete JSONB predicates, and SQLite `MAX` scalar with PG `GREATEST`.
   A duplicate invalidation lookup is shared with cognition's registry.
3. `postgres_runtime_agenda.py`:
   owner-only verified Discord DM and presence checks, event-ledger reads,
   deduplicated due reminders, cancellation, expiry and delivery status.
4. `postgres_runtime_temporal.py`:
   verified latest-owner-DM anchor, source invalidation, latest receipt,
   presence, delivered response and pending-work predicates. SQLite
   `julianday` is replaced with an explicit `timestamptz` ordering/comparison.
5. `postgres_runtime_episode.py`, `postgres_episode_lab_schema.py`,
   `memory/postgres_episode_fence.py`: *opt-in only* D.3C.2A sensory queue
   candidates, with FK-backed evidence, bounded lease/CAS and atomic
   memory+postings+queue acknowledgment. This does **not** install or activate
   PostgreSQL schema v19 in real services.
6. `messaging/storage_lane.py` now constructs the PostgreSQL-specific
   messaging store for the injected candidate, rather than falling back to the
   SQLite dialect inside a remote worker.
7. `AmoraDatabase.integrity_check()` now returns
   `postgres-candidate-unverified` after its structural preflight in
   candidate mode. The Core health status therefore remains **degraded**,
   rather than misleadingly returning `ok` from a SQLite PRAGMA.
8. `postgres_runtime_registry.py` assembles all Run 1–3 reviewed SQL domain
   families and fails on duplicate source SQL or conflicting infrastructure
   overrides. No dynamic SQLite SQL translation occurs.
9. Added `tests/test_postgres_run3_sql_inventory.py` and extended the
   existing contract tests and synthetic CI change paths. Tests are authored,
   **not claimed to have passed**.

## Diagnostics performed during Run 3

- Inspected canonical code paths in `app.py`,
  `generation_lifecycle.py`, `reflection_queue.py`, `agenda/service.py`,
  `temporal_initiative.py`, `episode_work.py`, `episode_fence.py`,
  `replica_coherence.py`, `messaging/storage_lane.py`, and main stores.
- Scanned the reviewed `add()` SQL templates of Run 1–3 for duplicate
  *literal* source keys and counted their explicit `%s` bind placeholders.
  No mismatched arity was detected in the **parsable literal subset**,
  including the Run 3 agenda, temporal, reflection and episode templates.
  Calls using assembled strings, inherited templates and dictionary entries
  were NOT covered by this limited analysis and must not be presumed correct.
- Inspected SQLite-only query operators in canonical code. Hidden legacy
  patterns include `json_extract`, `json_each`, `julianday`, SQLite
  `FTS5`, `PRAGMA`, `sqlite_schema` and dynamic `IN (?)` bindings.
- Separated *original serving* methods from their PostgreSQL subclass
  overrides for an executable static SQL inventory. The test must run in the
  later validation stage; a static match does not prove database behavior.

## Critical open release blockers — do not mark production ready

| Area | Missing proof or implementation | Release impact |
|---|---|---|
| PostgreSQL native data migration | Existing importer only accepts invented SQLite in-memory v18 fixtures to disposable localhost PG18. A secure, consistent importer for the actual 51-table/48,059-row source and freshness/cross-store reconciliation is missing | **Blocks cutover** |
| Schema v19 | PostgreSQL v19 DDL, version reconciliation, operator-approved upgrade/recovery and historical data retention are not certified. The episode candidate is auxiliary and optional | **Blocks episode promotion** |
| Replica epoch coherence | Original `amora_projection_epoch` and `amora_memory_projection_epoch` require authoritative monotonic writes/triggers and restore epoch rotation in PostgreSQL. `PostgresMemoryStore.record_verified_access()` continues to fail closed | **Blocks replica-coherent reads** |
| Native snapshot/recovery | `PostgresBackendCandidate.backup()` rejects SQLite file-copy semantics. Consistent PG export/PITR, restart reconciliation, failed-commit handling and operator rollback are not implemented | **Blocks cutover** |
| Per-principal disclosure | Original `ConversationStore` exposes internal unscoped list/get methods. Identity authorization must be proved at all public API/Discord entry points, including worker lanes | **Blocks production** |
| Memory/BM25 | SQL exists for transactional postings, but lexical normalization, FTS5 ranking parity, tie order, partial indexes and throughput need real PG18 side-by-side lifecycle tests | **Blocks production** |
| Reflection/generation/messaging | Lease takeover, stale edits, cross-worker claim races, outbound exactly-once-ish delivery and source invalidation require multi-connection functional tests | **Blocks production** |
| Full Core & optional components | Auxiliary paths for schema19 operators, legacy SQLite backup/restore, avatar archive, PCS.4C reports and read-only native replicas remain SQLite-specific by design, not automatically translated | **Blocks universal PostgreSQL claim** |
| Python compatibility | No complete integrated Python 3.11, 3.12, 3.13, 3.14 / PG18 test matrix has been certified | **Blocks production** |

## What must happen next

Perform the *separate* repair and comprehensive validation cycle authorized
after the three coding runs: dependency/code audit and import/syntax checks,
full synthetic PG18 Core boot, request→generation→memory reflection→retrieval
→voice/Discord queue→ack/cancel→restart loops, privacy/ACL cross-user attempts,
transactions under concurrent workers, FTS5/BM25 parity, v19 gate,
Python 3.11–3.14 matrix, snapshot and rollback. Repair failures on the
feature branch, repeat until behavior—not just CI status—is certified.

**No real Aiven writes, PCS.4C/Turso access or production migration, no main
merge, no deployment and no backend environment cutover were performed.**

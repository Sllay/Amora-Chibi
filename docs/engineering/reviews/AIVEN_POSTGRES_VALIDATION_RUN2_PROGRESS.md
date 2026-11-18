# Aiven PostgreSQL — Validation Run 2, correction and integrated verification

Branch: `fix/aiven-run2-integrity-20261118` (draft [PR #289](https://github.com/Sllay/Amora-Chibi/pull/289), base: `feat/aiven-full-runtime-integration`).
**Status: IN PROGRESS — no completion, cutover or main merge authorized.**
All PostgreSQL results below are disposable local PG18 and wholly synthetic.

## Bugs reproduced and corrected

- **Deep owner recall:** canonical owner-memory lookup formerly scanned only 512 newest rows. Review-backed query `PG_MEMORY_OWNER_RECALL_FTS` reads the entire eligible PG postings corpus before the fallback scan, with row cap and owner/public/privacy/time filters applied first. Test includes an old indexed memory behind 540 newer rows, 270 recent FTS tag-only distractors and private/other-owner checks. It reports `complete=False` when legacy unindexed records still exist rather than overstating search completeness.
- **Cognition V1 inheritance:** original `PostgresCognitiveStore` inherited the primitive store and lacked V1 helpers `usage_ledger`, `latest_usage`, `merge_message_metadata` and `record_interrupted_progress`. Fixed to inherit canonical V1 store and added reviewed PostgreSQL mappings for V1 usage and interrupted speech. Metadata merging uses atomic JSONB `||`, avoiding race-prone read-modify-write.
- **Telemetry errors:** best-effort usage recorder previously only caught `sqlite3.Error` and could abort cognition on psycopg failures. PG SQL failures and unknown commit outcomes are separately classified, without automatic retry; unrelated coding errors still propagate. A subsequent cloud-container run found a missing optional `psycopg` import wrongly masking a TypeError; corrected to preserve the original exception. Tested in both PG18 and dependency-light container.
- **Lost memory decay updates:** two writers could read the same old retrievability and overwrite each other. Replaced with one PostgreSQL `UPDATE ... retrievability*factor RETURNING` statement. Real PG two-worker regression proves 1.0 -> 0.5 -> 0.25.
- **Episode provenance test:** event fixture initially violated Core's canonical sensory event contract; test now uses `build_observation_event(ObservationContext,SensoryObservation)`. Opt-in v19-candidate episode test exercises a stale lease, authorized memory+search+queue completion and post-ACK replay rejection without enabling v19 or production.

## Synthetic PG18 integration tests added

- `test_postgres_run1_memory_integration.py`: full indexed recall with 540 newer rows, tag-only distractors, unindexed corpus honesty, owner/public/share isolation.
- `test_postgres_run2_core_bootstrap.py`: real `AmoraCore.bootstrap` startup, health semantics, V1 usage ledger persistence, shutdown/restart, connection budget release.
- `test_postgres_run2_usage_boundary.py`: psycopg constraints vs unknown commits, type errors not swallowed.
- `test_postgres_run2_messaging_queue.py`: two different DB connections claiming one outbound, generation token fencing, ACK and restart.
- `test_postgres_run2_cognition_v1.py`: atomic JSONB metadata patches and spoken-prefix tracking across restart.
- `test_postgres_run2_rank_parity.py`: SQLite FTS5 vs PG BM25/RRF/MMR order and owner scope with accent-sensitive text.
- `test_postgres_run2_atomic_decay.py`: parallel PG updates without lost retrievability decay.
- `test_postgres_run2_episode_fence.py`: opt-in PG18 sensory schema, canonical evidence, atomic lease+memory completion.
- These are wired into the actual `aiven-postgres-lab.yml` runner; not only standalone, unexecuted tests.

## Verified gates

The PG18 lab, perception, provider/capability, cloud-container and security gates passed at **`4819df96219c40356d1aa761e2d94bb8da095b45`**. At the time of this note, the complete Python 3.11–3.14 Core matrix for that SHA is still running and must be checked separately before a final run status is claimed. PG18 lab success means the new episode fence test passed in the synthetic opt-in configuration.

## Remaining Run 2 blockers

1. `PostgresMemoryStore.record_verified_access` intentionally rejects replica epoch operations, pending PostgreSQL-native projection epoch/restore-fence implementation and end-to-end tests. Never replace this with an unverified no-op.
2. Production-safe complete-source import, reconciled snapshots, restore/PITR and rollback are not implemented; old SQLite/Turso backups are NOT proof of real PG recovery.
3. PG v19 opt-in sensory queue is not an approved full database migration; recovery, cross-event invalidation and live scheduler/worker end-to-end parity still need tests.
4. Original Core API/Discord end-to-end inbound→generation→reflection→outbound→ACK/cancel under multi-worker retries and privacy grant invalidation still requires dedicated regression tests.
5. Full postings parity on a broad, realistic Unicode corpus and full-corpus indexed search with tag-only and unindexed legacy records needs independent stress/verification; the bounded scan explicitly reports incompleteness where it cannot prove otherwise.
6. Concurrent schema and data recovery, backup commitments and cross-principal authorization must be verified against synthetic PG18 before even considering a main merge.
7. External Aiven is not being used as a validation target; production remains unchanged.

**No merge to main, Aiven writes, Turso migration, production deployment or environment cutover.**

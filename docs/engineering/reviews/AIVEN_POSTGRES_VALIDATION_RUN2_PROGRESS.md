# PostgreSQL / Aiven — Run 2 final: candidate fixes and validation

**Run 2 code-validation status: COMPLETED for the synthetic PostgreSQL 18 candidate.**
**Production migration status: NOT CERTIFIED; NO CUTOVER.**

- Repository: `Sllay/Amora-Chibi`
- Correction PR: [#289](https://github.com/Sllay/Amora-Chibi/pull/289), **merged** on 2026-11-18 into **`feat/aiven-full-runtime-integration`**, not `main`
- Validated PR HEAD: `1e033a93597469f599b8bf7902e9f164e4e8bd72`
- Squash merge: `4432e142e499752bc1bf325d4e4241c4d0cd6394`
- Coverage is an explicitly injected candidate, isolated localhost PostgreSQL 18 and invented records. This report never claims production Aiven readiness.

## Final executable gates

All **eight** GitHub workflows succeeded on the validated PR HEAD:

| Workflow | Final run | Result |
|---|---|---|
| Aiven PostgreSQL 18 synthetic memory lab | [44249606857](https://github.com/Sllay/Amora-Chibi/actions/runs/44249606857) | success |
| Core tests (Python 3.11, 3.12, 3.13, 3.14) | [44249606837](https://github.com/Sllay/Amora-Chibi/actions/runs/44249606837) | success, 4/4 |
| Cloud container contracts | [44249606831](https://github.com/Sllay/Amora-Chibi/actions/runs/44249606831) | success |
| Pre-deploy security | [44249606817](https://github.com/Sllay/Amora-Chibi/actions/runs/44249606817) | success |
| Perceptual expression memory | [44249606827](https://github.com/Sllay/Amora-Chibi/actions/runs/44249606827) | success |
| Response modality competition | [44249606889](https://github.com/Sllay/Amora-Chibi/actions/runs/44249606889) | success |
| Capability grounded intent | [44249606814](https://github.com/Sllay/Amora-Chibi/actions/runs/44249606814) | success |
| Provider certification | [44249607182](https://github.com/Sllay/Amora-Chibi/actions/runs/44249607182) | success |

`python -m compileall -q core/amora tests` is a required step in the PG18 job. All PG18 integration tests included in that workflow passed on the final PR HEAD.

## Defects corrected and proven by regression

1. **Owner memory historical recall and privacy:** fixed missing FTS fallback, introduced owner-scoped full-index query and bounded legacy scan, and tested an old true match after 2,200 newer tag-only distractors plus unindexed records. A bounded scan reports incomplete if it cannot prove exhaustive coverage, rather than silently dropping private-memory matches.
2. **Canonical cognition V1:** corrected PostgreSQL subclass inheritance; reviewed usage-ledger, latest usage, atomic JSONB metadata merge and interrupted-speech persistence, including restart.
3. **Telemetry failure semantics:** PostgreSQL driver errors, ambiguous commits and unrelated programming errors are distinguished without masking the original error if the optional driver is not installed.
4. **Concurrent memory access:** changed retrievability decay from stale read-modify-write to one atomic PostgreSQL update and proved two simultaneous 0.5 multipliers yield 0.25. An opt-in memory epoch/revision fence rejects obsolete, missing, combined-scope and restored snapshot stamps before verified access; tested via multi-connection PostgreSQL.
5. **Generation, queue and reflection:** real PG stores tested for two-consumer lease arbitration, invalid lease rejection, deduplicated outbound, ACK, restart, invalidated-source reflection cancellation, stale completion rejection, new-revision eligibility and update of source-scoped derived memories.
6. **Sensory episode candidate:** real opt-in PG18 episode queue tested with canonical perception evidence, stale lease takeover, atomic memory/index/queue commit, restart recovery and after-the-fact evidence invalidation. This is NOT an approved serving schema v19 migration.
7. **SQLite/FTS5 vs PostgreSQL/BM25:** checked private/public/user scoping, accent handling, ranking order and Unicode synthetic corpus parity; candidate SQL remains reviewed and parameterized.
8. **Complete Core and Discord-like flows:** actual `AmoraCore.bootstrap`, cognitions, session/turn lifecycle, unknown user quarantine, verified owner DM, reply deduplication, edits, transport invalidation, outbound ACK, group privacy and restart exercised with synthetic transport messages and mock providers. No real Discord API calls or real provider inference.
9. **Perceptual expression/figurinha memory:** registered all canonical literal SQL in explicit reviewed PostgreSQL statements, tested metadata observation, scoped hydration, visual claim lease, semantic enrichment, idempotent retry and restart.
10. **Durability:** verified native `pg_dump`/`pg_restore` of the disposable PG18 schema and epoch rollover; exercised an exact, typed v18 synthetic SQLite import with 2,400 invented memory rows, binary avatar fixture, privacy scopes and FTS documents. No private source backups were opened.

The committed tests include `test_postgres_run2_discord_core.py`, `test_postgres_run2_discord_pipeline.py`, `test_postgres_run2_conversation_lifecycle.py`, `test_postgres_run2_expression_lifecycle.py`, `test_postgres_run2_reflection_queue.py`, `test_postgres_run2_episode_worker.py`, `test_postgres_run2_replica_epoch.py`, `test_postgres_run2_native_backup_restore.py`, `test_postgres_run2_scaled_import.py` and related memory/cognition/ranking/inventory tests.

## Explicitly separate external release gates (NOT certified by synthetic Run 2)

These require independent authorization and evidence *before* the Amora deployment/storage cutover:

- A production-capable Aiven connection with verified TLS, secret handling, real network conditions and controlled database permissions. The current `postgres-candidate` is intentionally loopback-only and is NOT the production backend environment selector.
- A privacy-preserving import from the actual Turso/SQLite source with its real table/row/blob counts, snapshot consistency, reconciliation, concurrent writes/freeze and operator-approved rollback. **The invented 2,400-row test is not proof of real data migration.**
- Aiven-managed backups/PITR and real recovery drills. PG18-in-Docker `pg_dump`/`pg_restore` is not an Aiven PITR guarantee.
- Full serving schema v19 upgrade/promotion, replica topology, final deployment configuration and live Discord transport/provider checks.
- End-to-end operator acceptance on the deployed Amora, with logs proving expected behavior before switching traffic.

**Decision:** no known remaining failing test in the Run 2 candidate-validation scope on the verified HEAD. This means Run 2 repairs are accepted into the *integration branch*. It does not prove that every possible bug is absent or that the real Aiven migration is complete. Run 3 may start on code optimization/quality in the integration branch; production stays blocked by the independent external release gates.

**Safety record:** no `main` merge, production Aiven writes, private Turso import, live Discord sends, actual production deployment or environment cutover was performed by Run 2.

# Aiven Phase 2D.1 — JSON and memory privacy predicate parity (synthetic)

Status: branch-only laboratory contract. **No production backend switch, no Aiven data writes or real backup import**.

## Tested source behavior

The canonical SQLite v18 storage code defines privacy boundaries in `core/amora/memory/store.py` and `core/amora/memory/disclosure.py`. The current production Core uses SQLite/libSQL SQL JSON1 queries such as `json_extract`, `json_each`, `json_array_length`, and restrictive owner/audience/recipient and invalidation predicates.

`core/amora/storage/postgres_privacy_lab.py` implements **only three specifically reviewed PostgreSQL SQL read predicates** using `jsonb`:

1. `projection_candidate_ids`: pre-FTS baseline candidate selection (at most 300), owner/audience/actor recipient, PUBLIC, AUTH/SECRET exclusion when recipient provided, remote-only, archived and date filtering. No raw text.
2. `owner_turn_memory_ids`: exact owner and `turn_id` metadata selection; rejects AUTH/SECRET; returns IDs only.
3. `shareable_owner_record_id`: manually mapped restrictive `SHAREABLE_MEMORY_SQL`, including valid dates, audience, derived sources, user assertions or owner-DM backed inferences, test-mode exclusion and reflection invalidation revision checks.

The separate CI suite loads invented SQLite v18 records into the 49-table PostgreSQL laboratory using the certified synthetic importer and executes matching queries on both databases. Tests include exact versus substring audiences, multiple owners, PUBLIC/AUTH/SECRET, local-only, archived and expired records, copied DM provenance and edited/deleted-source invalidation.

## Strict limitations

- The baseline candidate query does not include SQLite FTS5 lexical expansion, its `bm25` ranking, RRF/MMR or canonical hydration. Those remain a separate gate.
- These SQL functions are **not a complete production dialect**. All return IDs only; no Core integration. Only a PostgreSQL connection to loopback `amora_pg_synthetic` is allowed.
- The tests compare a finite synthetic fixture, not every Unicode, JSON, NULL, invalid schema or real-world edge case. PostgreSQL `jsonb` conversion semantics, especially JSON number/string/boolean casts, require further adversarial tests before a real importer.
- Never silently broaden disclosure to achieve query equivalence. If semantic divergence is found, fail the gate and adjust explicit SQL.
- Before production cutover, certify the full backend adapter, transactions/leases/concurrent writes, all privacy scopes, ranking and recall, realistic snapshot import, restart, rollback and source freshness.

All previous steps remain isolated in PRs #269–#273. No Northflank action is required.

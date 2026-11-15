# PCS.1 — local storage and replica laboratory

## Boundaries
Stable runtime reference: 382e8b1398a20a3474a43c1e779e22157f98bc61, branch backup/pre-pcs-stable-2026-11-14. PCS.0 isolated restore regression: PR #222.
This PR changes no Core, Worker, provider, V3, schema or runtime dependency. Local SQLite is an isolated *upper-bound* comparison, not a production replica. The remote arm uses current libSQL/Hrana over mock transport backed by real SQLite; 20 ms delay per pipeline is synthetic. Three fixed-clock context samples and three DM samples per arm; no real inference or reflection worker. Report median/max, not statistical p95. Context and rendered payload digests are compared without printing private prompts or profile content. Current provider-specific behavior is preserved rather than changing the profile path.

## Reproduce
- `pytest -s tests/test_pcs1_local_storage_lab.py` — creates synthetic remote fixture, exports snapshot, restores local, compares full context, trusted protocol and actual Numes payload, validates DM delivery/dedupe.
- `tools/cloud/pcs1-replica-probe.py` — pinned libsql 0.1.8 (classic protocol, lab only), disposable loopback sqld only, no production env variables. Core CI (Python 3.12, PCS lab branches only) starts a pinned server image. Tests read-own-write, FTS5, JSON, cross-replica sync, transaction rollback, unique constraints, multiple handles, DB-API surface and rebuilding a new replica. Numeric output includes peak RSS of probe alone, not Core or Northflank.

## Observed compatibility failure
Native SDK 0.1.11 was installed successfully in Python 3.12, but connect(sync_url=...) against pinned sqld 0.24.33 failed with HTTP 404 `failed to pull db export` before any test write. Upstream [0.1.11 source](https://github.com/tursodatabase/libsql-python/blob/v0.1.11/src/lib.rs) uses `new_synced_database` with remote_writes, whereas [0.1.8 source](https://github.com/tursodatabase/libsql-python/blob/v0.1.8/src/lib.rs) uses `new_remote_replica`. The local sqld fixture does not certify the newer cloud sync endpoint. Testing 0.1.8 separately identifies the classic protocol behavior; it is not a recommendation to downgrade production or a claim of current cloud compatibility.

## Design decision
Official docs: https://docs.turso.tech/sdk/python/reference and https://docs.turso.tech/features/embedded-replicas/introduction .
Embedded replicas keep local reads but send writes/write transactions to the primary; other replicas need sync. This is a better initial candidate than local-first write-behind on ephemeral storage because confirmed writes must survive container loss. Do not assume multiple SDK handles share read-your-writes or that DB-API exceptions/row types match Python sqlite3. No silent fallback to a fresh DB. No direct sqlite3 access to an SDK-owned replica file.

## Promotion gates
1. Native SDK contracts, including actual full Amora schema, FTS5, BLOBs, transaction semantics, row mapping, constraints, cancellation/ambiguous outcome and multi-connection freshness.
2. Authoritative primary checks for identity, permissions, invalidation, explicit memory and durable delivery; stale replica must not expose revoked/deleted data. Read-only projections need explicit freshness and bounded memory. No replica writes while disconnected.
3. Validate actual Northflank RSS/cgroup limit, disk, CPU and Core/Discord deployment details. A small native probe does not prove a 500 MB production fit.
4. Live recovery on isolated authorized Turso clone, not original; preserve rollback baseline and no schema changes until compatible.
5. Benchmark same messages and exact payloads, then real manual conversation approval. Memory-policy changes separate from storage migration.

No real Turso credentials used. No production migration authorized by passing this lab.

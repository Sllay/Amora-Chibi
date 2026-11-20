# Amora / Aiven — Run 3: final engineering readiness before live integration

**State:** IN PROGRESS. This is an executable engineering checkpoint, not a production migration certificate.

**Source:** `feat/aiven-full-runtime-integration`, after the validated Run 2 PR #289.  
**Working PR:** [#290](https://github.com/Sllay/Amora-Chibi/pull/290), targeting the feature branch only.  
**Production:** `main`, Turso, Aiven, Northflank and the live Discord bot remain unchanged.

## Engineering acceptance for Run 3

1. **Offline backup gate:** `postgres_run3_snapshot_preflight.py` reuses `pcs4_recovery.verify_snapshot` on an operator-controlled private, durable snapshot and its `.avatar.zip` sidecar. It verifies SQLite integrity, FK constraints, v18, assets and immutable source bytes. It also compares canonical v18 DDL against a synthetic migration fixture. The report contains aggregate counts and sizes only, never file paths, SHA values, database rows, usernames or secrets.
2. **Concurrency/load gate:** disposable PG18 processes 96 invented outbound messages through four independent worker connections; no duplicate claim, no lost ACK, no queued residue, durable restart and closed connections. A generous 90-second CI bound detects severe regressions, not a real service latency SLA.
3. **Regression gate:** the complete Core Python 3.11–3.14 matrix, PostgreSQL 18 integration, pre-deploy security, container contracts, memory perceptual and provider workflows must all succeed on the **same PR HEAD**. No stale green check is acceptable.
4. **Integrity and privacy gate:** synthetic cross-owner memory, identity quarantine, private/group isolation, source-edit cancellation, speech barge-in, queue lease and epoch/recovery tests from Run 2 must remain green.
5. **Documentation:** list all remaining release blockers and define exactly what the operator must prove before actual integration.

## Operator-only snapshot preflight (do not run in public CI)

Only after confirming that the source is a **private, durable, consistent** PCS.4C v18 snapshot and that the paired sidecar is retained:

```sh
python -m amora.storage.postgres_run3_snapshot_preflight \
  --snapshot /absolute/private/mount/snapshot.db \
  --confirm-offline
```

The module reads the local files only. It cannot connect to Aiven or Turso, cannot import data, and cannot change the Core backend. Success means **verified offline structure**, not source freshness, available PostgreSQL capacity, an Aiven restore, a production schema migration or cutover approval. Any failure emits only a stable error code.

## Release blockers that synthetic Run 3 cannot remove

- **Actual source snapshot:** confirm the existing PCS.4C `v18` source and sidecar are accessible in the private Northflank volume; record immutable checksums *privately* and verify that no newer data was written after the snapshot. The historic 51-table / 48,059-row report is not a live source guarantee.
- **Capacity:** measure real private backup sizes and Aiven free-tier storage headroom, including PostgreSQL indexes, WAL, restore overhead and replicas. Never guess capacity from row count alone.
- **Production migration:** implement and certify a *separate* privacy-preserving importer, with real source/target typed-row and avatar parity, an atomic/controlled source freeze, resumable or rollback-safe behavior and final freshness reconciliation. The synthetic importer must never be reused on private data.
- **Aiven recovery:** validate TLS `verify-full`, least-privilege permissions, retention/PITR, backup and restore using an isolated Aiven environment; local Docker `pg_dump` is not equivalent.
- **Runtime promotion:** a reviewed serving PostgreSQL backend selector, production-safe schema qualification, v18/v19 decision, memory replica fencing, resource/connection budget and fail-closed startup checks are still required. Current `postgres-candidate` is synthetic and not selectable by `AMORA_STORAGE_BACKEND`.
- **Live acceptance:** deploy first to an isolated staging service with non-private test identities; test actual Discord DM, provider response, memory recall, speech interruption, cancellation, restart, image/figurinha and audit logs. Only then consider a user-controlled production cutover, with a verified rollback path.

## Decision semantics

- **CI green:** the code candidate is eligible for review/merge into the **feature integration branch** only.
- **Offline snapshot verified:** the private snapshot may proceed to an independently authorized migration rehearsal; this is not permission to upload it to CI or to mutate Aiven.
- **Live migration approved:** requires all external release blockers above to be resolved with evidence. No automatic deployment, secret access, schema v19 promotion, data migration or `main` merge occurs in this Run 3 PR.

**No timers, scheduled tasks, recurring checks or new chats are created by this run.**

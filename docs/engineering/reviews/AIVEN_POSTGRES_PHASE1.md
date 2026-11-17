# Aiven Phase 1 — offline audit of certified PCS.4C backup

Status: isolated engineering candidate. **Do not merge/deploy until PR CI passes.**
Related issue: #268. This is not a PostgreSQL adapter, data import, or schema migration.

## Operator facts

- Aiven PostgreSQL 18 preflight returned `status=ok`, `read_only=true`, `tls=verify-full` and exit 0 on 2026-11-17.
- Operator confirmed the existing PCS.4C persistent volume is still attached to the manual Job at `/private`.
- PCS.4C recovery code writes the snapshot to `/private/pcs4c/backup/amora-pre-pcs4c.db`, accompanying `.avatar.zip`, and certification state to `/private/pcs4c/state.json`.
- Historic certified report: schema 18, 51 logical tables, 48,059 rows, 0 assets, backup/verify/restore/restart OK. The live retained files have **not** been reaudited as part of this PR.

## Why this tool exists

Calling `python -m amora.storage.pcs4_recovery verify ...` directly emits SHA-256 hashes into logs. For operator-friendly reports, the new `aiven_snapshot_audit` module reuses exactly the existing `verify_snapshot` logic, but prints **only sanitized aggregates** and predefined failure codes.

It:
1. Refuses missing snapshot/state or an incomplete/active PCS.4C certification.
2. Runs the PCS.4C SQLite integrity, FK, sidecar, asset-manifest, data/hash and schema 18 validation in read-only mode.
3. Compares source and sidecar hashes **privately** with the hashes pinned in `state.json`, and validates stored backup/verify/restore counts.
4. Counts structural SQL objects without reporting table names, schema definitions or data, and records database/archive byte sizes.
5. Checks that `state.json` did not change while reading. Never creates, uploads, imports, deletes, or updates database contents. No PostgreSQL or Turso connection is created.

It emits an `AIVEN_SNAPSHOT_AUDIT` JSON line with `status=ok` or `status=blocked`; hashes, secret strings, paths and record content are excluded.

## Operator execution (only after CI green and approved image is available)

Use the **existing** PCS.4C Manual Job and its persistent volume; do not create a new paid service or volume. The Northflank UI lets an operator [preview and override configuration for an individual manual run](https://northflank.com/docs/v1/application/run/run-an-image-once-or-on-a-schedule). In the run preview, use an **execution-scoped command override** (not a permanent job CMD edit) when available:

```text
python -m amora.storage.aiven_snapshot_audit
```

The one-off job **must** run a Core image that includes this module and mounts the same persistent volume at `/private`. Preserve the PCS.4C job's normal configuration, original CMD and state. Never use the isolated `deploy/Dockerfile.aiven-preflight` image: it deliberately contains only the Aiven connectivity checker and does **not** contain the snapshot auditor.

Do not run if the user interface does not provide an execution-scoped override, or if enabling this command would alter the normal job permanently. Confirm resources are covered by the existing zero-cost allowance before an execution.

Successful output shape:

```json
{"status":"ok","phase":"aiven-phase1-offline","read_only":true,"schema_version":18,"tables":51,"rows":48059,"certification_hashes_match":true,"postgres_import_performed":false}
```

Actual output includes other aggregate counters and sizes; the values above are historical expectations, **not evidence of a new run**. If blocked, report only `reason`. Do not publish file contents, hash values, raw stack traces or connection strings.

## What a successful audit does *not* prove

- The snapshot contains the latest data. Any changes after backup capture, while Turso was accessible, remain outside it.
- PostgreSQL can execute the SQLite/FTS5 schema unchanged. It cannot.
- The data plus indexes will fit the free Aiven 1 GB allocation. That needs capacity engineering.
- Runtime PostgreSQL compatibility, privacy, rank semantics or an operational rollback.

Next engineering gate: map the SQL stores, design the PostgreSQL schema and full-text/JSON equivalence and run synthetic compatibility tests without live memory data. Preserve v18 source and keep `AMORA_STORAGE_BACKEND` unchanged until a separately certified cutover.

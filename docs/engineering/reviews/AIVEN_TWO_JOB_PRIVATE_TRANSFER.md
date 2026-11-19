# Aiven PostgreSQL 18 — two existing Northflank Jobs, no volume reassignment

**Status: reviewed code candidate in development. Neither live Aiven DDL
nor private PCS4C import is authorized by this document.**

## Real operator evidence (19 November 2026)

- Aiven PostgreSQL 18 read-only connection passed (TLS verify-full).
- Read-only capacity inventory passed: 8,181,439 database bytes, 20 max
  connections, zero user tables and no staging namespace. Aiven Free offers
  1 GB *total* storage, but the exact available disk/headroom has NOT
  been independently certified. WAL, temporary writes and BM25 indexes
  consume additional space.
- PCS4C backup pair at /private/pcs4c/backup/amora-pre-pcs4c.db verified:
  SQLite schema v18, 51 logical tables, 48,059 rows, 75,563,008 bytes,
  zero avatar assets, .avatar.zip sidecar and integrity checks passed.
  Operator says it is current. This is not independent proof of source freshness.
- Staging operator `inspect` returned `staging_preparation_possible` with
  zero application tables. **No staging schema has been created yet.**

## Why existing two Jobs suffice

Northflank's Single Read/Write persistent volumes can be mounted by ONE pod at
a time. The /private PCS4C volume remains attached exclusively to the existing
`pcs4c-recovery` Job. No copy through stdout, new public HTTP endpoint,
rsync daemon, CI artifact, GitHub release or user chat is acceptable for
private memories.

Instead, a dedicated image with the pre-reviewed importer can run on the
**existing pcs4c-recovery Job**, read the mounted source SQLite in
read-only mode, and connect outbound via TLS verify-full directly to the
isolated Aiven staging schema.

| Northflank Job | Default function | Volume | PostgreSQL access |
| --- | --- | --- | --- |
| `Aiven` | Read-only target inspection, optional manual prepare and read-only structural verification | None | Aiven DSN + CA only |
| `pcs4c-recovery` | Original PCS4C recovery; optional temporarily selected bridge image only after backup/rollback gates | EXISTING /private volume, never moved | Only for explicitly approved import window; no other services |

Original Turso and Core/Discord stay unchanged. Nothing here registers a
PostgreSQL serving backend or switches production.

## Stage A: write-free target verification (already passed)

Use the existing Aiven Job with Dockerfile
`/deploy/Dockerfile.aiven-staging-operator` and Docker CMD Default.
Its `inspect` mode makes metadata-only checks, no DDL. Keep the exact
3 endpoint pins `AMORA_STAGING_EXPECTED_HOST`,
`AMORA_STAGING_EXPECTED_PORT`, and
`AMORA_STAGING_EXPECTED_DATABASE`, from the Aiven Console.

## Stage B: approve one-shot EMPTY target schema (NOT YET PASSED)

Before invoking `prepare`, document real recovery, disk and isolation gates:
1. Original Turso stays available, production writers and exact snapshot
   freshness are accounted for. Keep the original sealed private pair.
2. Preserve an **independently stored encrypted backup** and prove independent
   retrieval/restore. A Northflank snapshot is useful defense-in-depth but is
   not off-provider restoration. Don't report a copy in the SAME /private
   volume as an independent backup.
3. Aiven Console confirms service Running, Free plan and sufficient disk
   headroom for tables, index, WAL and temporary workspace; static 1 GB
   nominal limit alone is not a capacity certification.
4. Verify exclusive isolation of the empty target and staging-only DB
   privileges. No Core or Discord clients may write into this database.
5. Authorize operator's existing exact confirmation. Its four boolean flags
   are attestations, NOT automated checks. Never set them without evidence.

The existing `Aiven` Job performs all database preparation, in a bounded
one-shot transaction. No SQL from personal data is used to create the
schema. After creation, the read-only verification command is:

    python -m amora.storage.aiven_staging_operator verify

This prints `AIVEN_STAGING_OPERATOR`, with
`status=staging_structure_verified` and
`structural_state=preimport_structural_pass`.
This is structural verification, NOT actual row parity.

On any ambiguous write outcome, **do not repeat prepare**. Use the above
read-only verify command to inspect first.

## Stage C: source bridge (only after B passes)

Preserve the existing pcs4c-recovery Job runtime config, image revision, and
volume mount. Disable CD, schedules, retries and overlapping runs.

Once approved, temporarily point only this Job's Dockerfile to
`/deploy/Dockerfile.aiven-private-pcs4c-bridge` on the integration
branch at a pinned, tested revision. The image deliberately preserves non-root
UID 10001, includes psycopg3, and DOES NOT contain private backup bytes.

The image's **default** is offline/readonly:

    python -m amora.storage.aiven_private_import_operator inspect

This inspects the existing /private mount, revalidates the PCS4C
state.json hashes and backup+sidecar, checks exact v18 migration DDL,
and prints only aggregate numbers. No connection to Aiven or Turso,
no writing to /private, and no private contents in logs.

The import command is a separate, one-shot **custom command** requiring
source freshness, external encrypted backup/restore, target isolation,
free-tier capacity attestations, plus the exact import literal.
Do NOT invoke it while any gate remains unverified. It does not
create the schema: it requires a previously prepared empty schema,
copies typed rows with full parity into a transaction, reconstructs
FTS5 token counts/postings from an in-memory source, and preserves
original SQLite/sidecar bytes and Turso.

The importer does not serve Core, Discord, or external chat traffic.
It never retries an unknown transaction outcome; failures after connecting
must be treated as requiring a human review of the destination.

## Stage D: restore the original Job and release gates

Restore pcs4c-recovery's prior Dockerfile, branch, pinned build, and
default operator command after the authorized import. Confirm the original
/private volume still mounts at exactly the same path, and verify again.

Run staged PostgreSQL parity/ACL/BM25/voice/restart validation and
independently tested backup/restore, then implement and certify the Core
PostgreSQL serving selector and psycopg dependency. Production cutover
and `main` merge remain blocked until all release gates are green.

No paid upgrade, third Northflank Job, volume reassignment or data
exposure is part of this design.

Official references:
- https://northflank.com/docs/v1/application/databases-and-persistence/add-a-volume
- https://northflank.com/docs/v1/application/databases-and-persistence/backup-and-clone-volumes
- https://aiven.io/docs/products/postgresql/concepts/pg-free-tier
- docs/engineering/reviews/AIVEN_PRODUCTION_RECOVERY_AND_CUTOVER_GATE.md

# Aiven PG18 — isolated one-shot schema preparation (manual operator)

**State: development candidate. NOT an approval to execute DDL in real Aiven.**
This manual operator is a different image and Job from the existing Aiven read-only
preflight and PCS4C recovery. It NEVER imports rows, reads Turso, mounts /private,
switches Core/Discord storage, retries a write after uncertain COMMIT, or alters
the production branch.

## Verified evidence before operator configuration

- Real Aiven preflight: PostgreSQL major 18, sslmode verify-full, read-only
  connection; exit 0, 2026-11-19.
- Real Aiven capacity inventory: 8,181,439 database bytes, 20 max connections,
  no user tables and no staging schema, 2026-11-19. That output does NOT verify
  free disk, plan Free or available storage.
- Operator PCS4C verify: source SQLite v18, 51 logical tables, 48,059 total
  rows, 75,563,008 bytes, 0 avatars; exit 0, 2026-11-19. Operator reports the
  source is current, but a snapshot-integrity test cannot establish data recency.
- External encrypted backup AND independent restore, Aiven free-tier disk headroom
  and operational isolation remain unverified. **Do not run write-mode until
  these gates have been satisfied and documented.**

## Safe first action: create a separate, manual inspection Job

In Northflank, create a new Manual Job dedicated to staging, e.g.
`aiven-staging-preparation`. Do NOT modify the existing `Aiven` Job,
`pcs4c-recovery`, `loyal-coat` or the Discord worker.

1. Source: GitHub `Sllay/Amora-Chibi` branch
   `feat/aiven-full-runtime-integration` at an explicitly pinned revision that
   contains this operator, after CI approval. No auto-deploy.
2. Dockerfile location: `/deploy/Dockerfile.aiven-staging-operator`.
3. CMD override: **Default configuration**. Default image command is
   `python -m amora.storage.aiven_staging_operator inspect`, read-only.
4. Manual run only; scheduling, CD and 'run on image change' OFF; no retries or
   overlapping executions. UID is 10001; do not run as root.
5. Only the Aiven `AMORA_POSTGRES_DSN` secret and the pre-existing
   `AMORA_POSTGRES_CA_FILE` secret-file mount (or
   `AMORA_POSTGRES_CA_PEM`), using the reviewed certificate location.
   Do not add Turso, Discord, provider keys or any `/private` volume.
6. Running default CMD should print `AIVEN_STAGING_OPERATOR` JSON with
   `status=staging_preparation_possible`, no user tables, no schema and no
   import. Copy only the sanitized JSON, never secrets/DSNs/certificates.

If this fails, do not execute a DDL action; inspect stable reason code instead.

## Pre-DDL gates: do not self-certify with placeholders

1. Preserve the original PCS4C backup and immutable sidecar on a private
   durable mount; verify no new Turso writes were omitted.
2. Maintain an **independent encrypted external backup**, validate retrieval and
   restore in an independent isolated environment, and record its evidence
   privately. One copy in the same /private volume is NOT independent recovery.
3. Confirm Aiven's console shows Free, service Running, and enough free disk
   budget for PG WAL/index/temp overhead (1 GB total disk is NOT 1 GB usable).
4. Establish that no app has the new Aiven DB credentials; no Core/Discord
   writers use this DB. A schema in the same Aiven service is **logical isolation,
   not a separate server**. Review privilege/role restrictions.
5. Verify the database/schema still empty immediately prior to DDL.

## Exact one-shot DDL command — ONLY once ALL gates are satisfied

Run the distinct staging Job with temporary **Custom command**; never change
the persistent CMD and never run this command on the small Aiven preflight image:

    python -m amora.storage.aiven_staging_operator prepare --confirm PREPARE_EMPTY_PG18_ISOLATED_STAGING_ONLY --snapshot-attested --external-backup-restore-attested --isolated-target-attested --free-plan-capacity-attested

All four booleans are human attestations, **NOT** independent technical proof.
The executor refuses the write before connecting if any one is absent.
It forces verified TLS, pinned endpoint/database/major18, and checks the
staging schema and all user tables are absent, including again inside the
DDL transaction. DDL is bounded by 5s lock and 60s statement timeouts.
It prepares canonical 49 relational tables and auxiliary search/memory tables
and runs a read-only postcommit structural preflight; migration ledger stays
empty, so serving remains blocked.

Success: `status=empty_staging_schema_prepared`, and
`postcommit_readonly_preflight=preimport_structural_pass` with
`private_import_performed=false` and
`production_cutover_authorized=false`.

**Uncertain exit/connection result: do not retry DDL.** Preserve destination,
collect sanitized diagnostics and review its state read-only. The operator
always exits 2 for blocked statuses and 0 for inspected/prepared statuses.
No force/recreate/drop option is provided.

## Next phase (separate authorization)

The 48,059 source rows are NOT imported by the schema operator. A separately
reviewed private staging importer requires a controlled private backup copy and
independent integrity/rollback validation. Core PostgreSQL runtime, release
to main, production data cutover and Discord tests remain gated.

References:
- `docs/engineering/reviews/AIVEN_EMPTY_STAGING_SCHEMA_GATE.md`
- `docs/engineering/reviews/AIVEN_PRODUCTION_RECOVERY_AND_CUTOVER_GATE.md`
- `docs/engineering/reviews/AIVEN_PRIVATE_STAGING_IMPORT.md`

# Aiven Free — live readonly capacity inventory gate

**Status: operator-read-only inspection, NOT a schema installer, import, recovery or Core cutover.**

## Why this gate exists

Aiven's published Free tier offers 1 GB disk and 20 concurrent PostgreSQL connections without pooling. A historical 75.6 MB SQLite PCS.4C snapshot does NOT predict PostgreSQL expansion (tables, indexes, WAL, temp files or recovery overhead). Before any writes to Aiven, inspect the actual Free PostgreSQL service and independently verify that its plan remains Free.

The pre-existing Job **Aiven** has a verified Aiven DSN and CA-mounted Secret File and a default command. This PR adds a second, optional Python entrypoint in its same tiny Docker image, leaving the **default command unchanged**. Core, Discord, Turso, and pcs4c-recovery Job are untouched.

## Operator-only inventory

**Prerequisites:**
- A pinned built image containing both aiven_preflight.py and aiven_capacity_inventory.py on the Aiven Manual Job. Do not assume a Job with CD disabled has automatically rebuilt after a GitHub merge.
- The Aiven Job retains the private AMORA_POSTGRES_DSN and AMORA_POSTGRES_CA_FILE/AMORA_POSTGRES_CA_PEM. Do not add the Core, Discord or Northflank admin tokens; remove temporary NORTHFLANK_API_TOKEN left by previous audit.
- A one-off **execution-scoped custom command** (never a permanent service CMD edit):
  python /opt/amora/aiven_capacity_inventory.py
- Keep Job Manual, CD OFF, no schedule/retries, and use the same free compute allocation. Confirm it is within the plan before running. Do not change the service Core default command.

The inspector reuses the existing TLS verify-full connection helper, ephemeral mode-0600 CA file, read-only session and sanitized error codes. It runs only seven static, parameterized metadata queries:
1. SHOW transaction_read_only
2. SHOW server_version_num
3. SHOW max_connections
4. SELECT pg_database_size(current_database())
5. Count *namespace metadata only* for the canonical staging schema.
6. Count *table metadata only* in that schema.
7. Count *non-system table metadata* in the entire current database.

It reads **no table rows, names of owners, memories, messages, identity IDs, blobs, FTS contents, passwords or DSN**. It does not request write credentials; operator decides if the supplied read-only session has more rights than needed. Any invalid metadata, unsupported PG version, TLS/CA failure or read-only violation blocks without source/target identifiers or stack traces in logs.

Example successful result fields: status=inspected_read_only; server_major=18; tls=verify-full; database_size_bytes=<number>; server_max_connections=<number>; staging_schema_exists=<boolean>; staging_schema_tables=<number>; database_user_tables=<number>.

**A success never certifies actual free plan, unused disk capacity, production readiness, schema integrity, import parity, backup retention, recovery or rollback**. These fields intentionally remain false. A database may show 0 user tables yet contain other objects or WAL beyond pg_database_size. Existing staging objects require operator review; do not overwrite or delete them.

## Release sequence

1. Approve this isolated inventory's CI. Do not change main or the active Core.
2. Build a **pinned image** for the Aiven Manual Job (its current CD is OFF), keeping the default command untouched.
3. Run a one-off inventory with the optional custom command. Share **only its sanitized JSON report**, never the DSN, CA, runtime secrets, or unfiltered Job environment.
4. Verify current Aiven plan in the dashboard and compare measured database size with estimated target size, index/WAL headroom and 20-connection budget. Reconcile private source freshness separately.
5. Only with an independently verified backup/restore plan and an isolated target should a future *explicitly authorized* staging provisioning/import Job be used. There is no Aiven schema creation or import in this PR.

Documentation:
- https://aiven.io/docs/products/postgresql/concepts/pg-free-tier
- https://aiven.io/docs/products/postgresql/reference/pg-connection-limits
- https://northflank.com/docs/v1/application/run/run-an-image-once-or-on-a-schedule

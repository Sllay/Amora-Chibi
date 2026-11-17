# Aiven PostgreSQL Free — Phase 0: isolated connection preflight

## User-confirmed context

The operator created service `amora-database` in Aiven project
`amora-ia` with service plan **Free-1-1gb** (PostgreSQL 18, one CPU,
1 GB RAM, 1 GB storage and max 20 connections). **Zero monthly spend is a
non-negotiable project rule.** No paid plan, trial credits or billing changes.

Current Turso organization is temporarily **BLOCKED** because the active
`amora-staging` database exceeded the Rows Read quota. Do not delete
`amora-staging`, reuse a Turso auth token as a PostgreSQL credential, or
depend on unblocking Turso to begin offline engineering.

## Architecture decision

Amora Core v18 currently supports `AMORA_STORAGE_BACKEND=sqlite|libsql`.
The code uses SQLite SQL, FTS5, JSON functions, pragmas, triggers and
DB-API semantics in many stores. PostgreSQL is **not** a drop-in replacement
for the existing libSQL backend, and the 51-table PCS4C snapshot cannot be
directly uploaded as a PostgreSQL database file.

Therefore migration is intentionally divided into certified stages:

0. **This PR:** isolated, read-only Aiven connectivity preflight. No
   `AMORA_STORAGE_BACKEND` changes, schema creation, table import, or
   production service wiring.
1. Verify latest PCS4C snapshot and v18/v19 candidate *without modifying*
   original backup. Count tables and rows, check provenance and timestamp.
2. Design/implement PostgreSQL schema and query dialect for Amora's storage
   interface. Cover transactions, SQLite predicates, FTS5, JSON, rank
   semantics, SQL placeholder conversion, and migrations without string
   substitution hacks.
3. Restore a **copy** of the snapshot to a new isolated PostgreSQL database,
   reconstruct search indexes, compare row-level identities, relationships,
   memory privacy and ranking, and restart with a realistic workload.
4. Switch Core on a separately approved, explicit flag after tests and
   rollback certification; preserve Turso credential configuration and
   the source snapshot. No production migration until certification.

The original v18 snapshot is not replaced by the v19 candidate, and the
operator must choose/verify the correct source first. Snapshot age may mean
recent memories are absent; reconciling differences must be a separate step
once Turso becomes accessible.

## How Phase 0 works

The standalone command:

```bash
python -m pip install -e '.[aiven-preflight]'
python -m amora.storage.aiven_preflight
```

reads two **private, unlogged** environment values:

- `AMORA_POSTGRES_DSN`: Aiven PostgreSQL **Service URI**, directly from
  Aiven Connections (not a Turso key). The URI includes username/password,
  hostname, port and database.
- `AMORA_POSTGRES_CA_PEM`: the PEM text of the **CA certificate** downloaded
  from that Aiven project. Use a dedicated Northflank secret; never paste
  either value into PRs, logs, Discord or ChatGPT.

The preflight validates the DSN is a PostgreSQL URI to an Aiven-hosted
endpoint, creates a mode-0600 temporary CA file, and **forces TLS
`sslmode=verify-full`** instead of Aiven's default `require`. It connects
with a default read-only session and performs exactly two metadata queries:
`SHOW transaction_read_only` and `SHOW server_version_num`. The certificate
file is deleted on exit. It issues **no writes, no DDL, no SELECT of user
data**, no Turso traffic. Only a sanitized status and server major version
are emitted, never DSN, password, hostname or certificate.

This preflight is opt-in and independent from Core bootstrap; it is
not automatically executed on every app restart. Absent credentials and
missing optional psycopg driver fail safely, and nothing in this PR
activates `postgres` as a serving backend.

**TLS reference:** https://aiven.io/docs/platform/concepts/tls-ssl-certificates
states that the default `sslmode=require` encrypts but does not verify
the server certificate; `verify-full` with the Aiven project CA also
verifies hostname.

## Stage 0 acceptance conditions

- [ ] CI passes all Core, storage, security and container tests.
- [ ] Fake-driver tests verify readonly query list, CA permissions,
  certificate cleanup, no secret leakage, and invalid DSN blocking.
- [ ] The operator places secrets in approved Northflank secret storage,
  without changing active `AMORA_STORAGE_BACKEND=libsql`.
- [ ] A preflight **run under operator control** returns sanitized
  `status=ok`, `read_only=true`, `tls=verify-full`.
- [ ] No live migrations, writes, imports, or unsupported assumptions
  about the contents of the PCS4C backup.

Only after this stage is complete may Phase 1 inspect snapshot data.

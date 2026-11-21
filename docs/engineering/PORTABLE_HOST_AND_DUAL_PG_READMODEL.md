# Portable hosting profile and Aiven PG18 / Altare PG17 read model

Status: **safe design and pure-code contract only**. No production dual-store routing, private data export, secrets access or Altare deployment is enabled.

## Distinguish server versions from schema versions

- PostgreSQL 18 at Aiven and PostgreSQL 17 at Altare are *server majors*.
- Amora schema v18 is an application schema revision, not PostgreSQL major 18.
- The current guarded Core PG18 adapter pins the destination, major and TLS certificate. It must NEVER be pointed at PG17 simply by replacing a connection string.
- The Altare cache uses an independently versioned read-model format (v1), not an unreviewed copy of the full schema.

## Source-controlled profiles

Edit `ACTIVE_DEPLOYMENT_PROFILE` only in `core/amora/deployment_profiles.py` after release approval. Present value: `northflank`; target candidate: `altare`. `tools/ops/show_deployment_profile.py` prints a non-secret plan. It does not deploy, recover secrets or switch DB connections. The host workflow consuming the plan remains to be implemented and tested.

## Canonical write and fast read responsibilities

1. **Aiven PG18 is the sole writer of record:** all accepted memory, identity, event, relationship, version, delete and privacy changes commit here.
2. **Altare PG17 is a small read projection only:** relevant, recent, non-secret owner-scoped memory excerpts. The free database has 256MB memory, 1GB storage and only three days of nightly backups, so it is not a second full history archive.
3. **Future transactional outbox:** every committed canonical change also appends an immutable event in the same Aiven transaction. A worker copies changes in commit order and advances a durable cursor after idempotent PG17 upsert/delete. No dual writes. Unknown commit result must be reconciled, not replayed blindly.
4. **Future reader:** start in shadow mode, comparing latency and authorized results while Aiven serves all user responses. Enable fast-cache reads only after validation. A known stale revision, missing canonical revision, expired record, revoked access or missing cache falls back to Aiven. Critical consistency reads always use Aiven.
5. **Fail closed:** owner ID, privacy scope, canonical sequence, TTL, archive state and content checksum must agree before a cached projection may serve a request. Never copy AUTH/SECRET or records that disallow remote processing.

Current `readmodel_projection.py` provides only a pure serializer and check predicate. It does NOT claim to implement an outbox, connection pool, data mover, private data migration, SQL schema translation or active two-DB serving.

## Reversible migration checklist

1. Verify Aiven PG18 backup/restore and memory schema parity independently.
2. Build Altare Core, Discord and Web services using the same immutable code release; keep a second production Discord gateway OFF.
3. Provision Altare PG17 with limited projection credentials; measure space and indexes against the free quota.
4. Use synthetic privacy and replay data to validate projection. Only after approved data policy, implement transactional outbox and bounded replication.
5. Run shadow queries and 72-hour host observation; record p50/p95 memory latency, lag, crash recovery, DB disconnect, and restart correctness.
6. After owner-approved cutover, fence the Northflank Discord worker and enable a *single* Altare gateway worker. Aiven remains authoritative.
7. Rollback to Northflank by switching the source-controlled profile, redeploying the tested code release, verifying the single Discord worker and continuing on the same Aiven authoritative database. This avoids reverse-importing memories.

Low latency from co-location is a hypothesis, not a guarantee: benchmark Aiven vs PG17 cache. PG native logical replication requires managed provider support and compatible versions; it does not automatically translate different logical schemas or authorize access. Prefer a purpose-built, reviewed projection outbox until verified otherwise.

**Secrets:** these profiles contain only nonsecret names and roles. Actual DSNs, CA certificates and host API keys must stay in approved secret managers. The desired release profile cannot reconstruct secret values from old hosts.

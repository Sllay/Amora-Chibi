# Amora Aiven — Recovery and production cutover gate (2026-11-18)

**State: BLOCKED for real production cutover.** Runs 1–3 certified disposable synthetic PostgreSQL 18 and Core Python 3.11–3.14. They **did not** certify private PCS.4C data, Aiven production, rollback, live Discord, the production Dockerfile, or a new storage backend selector.

## Current known infrastructure, not a live Northflank inspection

- Northflank team mejorberrys-team, project default, service loyal-coat (Core); Discord worker remains a separate existing service.
- The root Dockerfile has CMD amora serve and does **not** install psycopg3. Mainline AMORA_STORAGE_BACKEND accepts only sqlite|libsql. Merging a synthetic-only candidate into main **cannot activate a functional PostgreSQL serving runtime**.
- Manual Aiven job: Dockerfile /deploy/Dockerfile.aiven-preflight, Docker runtime Default, one read-only TLS-verified check. Tiny image has no Core, PCS.4C modules, or production importer. Give it only the Aiven DSN + CA file, never all Core secrets.
- Manual pcs4c-recovery job: uses the Core image, private persistent volume /private, UID 10001. Original recovery command is python -m amora.storage.pcs4_job, but it was temporarily changed to python -m amora.storage.aiven_snapshot_audit. Audit *current live state* before changing it back, preserving durable lock/state, the same volume, build, source and destination. Do not repeat restore with unknown job state. Keep CD off, image-change run Never, no cron/retry/concurrency.
- Historical PCS.4C report: 51 logical tables, 48,059 rows, schema v18, 0 assets, SQLite backup approx. 75.6 MB. This is **not evidence of source freshness or present backup retention**.
- Aiven amora-database, project amora-ia, Free plan: 1 GB total storage, 1 GB RAM, 20 max connections, no connection pool. Aiven Free does **not provide full PITR**; its price table describes one disaster-recovery backup. Do not create paid resources or assume a free fork.

## Gate A: read-only inventory of Northflank configuration

Use tools/ops/northflank_readonly_audit.py in an operator-controlled host (Termux is supported). It calls fixed GET endpoints for service/job details and deployment only. No actions, no secrets endpoints, no raw responses, no printed API tokens.

Command from the repository root:

    python tools/ops/northflank_readonly_audit.py --team mejorberrys-team --project default --core loyal-coat --pcs4-job pcs4c-recovery

The tool prompts for a scoped Northflank read-only API token without echo; do not paste secrets into chat, GitHub, logs or command-line arguments. Send only the tool's sanitized JSON output. For a nonstandard Aiven job ID use --aiven-job ID. A status of unknown is **not a pass**. Service/job credentials, private mount usability and source hashes need separate authorized verification.

| Resource | Expected Dockerfile | Expected command | Expected delivery |
| --- | --- | --- | --- |
| Core loyal-coat | /Dockerfile | Docker Default (amora serve) | CD **disabled during recovery**, active image SHA recorded; do not enable before deployment approval |
| Aiven manual job | /deploy/Dockerfile.aiven-preflight | Docker Default | No schedule, no automatic run, no Core/Turso/Discord secrets |
| pcs4c-recovery manual job | /Dockerfile | python -m amora.storage.pcs4_job **when restored to PCS.4C role** | CD OFF, Run on image change Never, no cron/retries, persistent private /private volume unchanged |
| Discord Worker | workers/discord/Dockerfile | Existing certified command | Keep credentials and independent worker; restart after Core readiness |

Never turn on CD or remove command overrides blindly: first compare the actual service resource and immutable image with the above.

## Gate B: private PCS.4C backup integrity and freshness

- Confirm that the actual .db + .avatar.zip pair still exists together on a durable private Northflank volume. Preserve source bytes and record the original and final SHA-256 **privately**, along with backup date and code revision.
- Reuse PCS.4C verifier or the operator-only offline module amora.storage.postgres_run3_snapshot_preflight in a job that contains the full Core Python modules and read-only private mount. Do **not** run it with the small Aiven preflight Docker image.
- Check SQLite integrity, foreign keys, schema v18, canonical DDL, archive manifest/files and row counts. Only publish aggregated sanitized codes.
- Identify any writes after the historical snapshot. If the Turso source is stale, reconcile missing updates under an explicit write-freeze protocol. Do not treat historic 48,059 rows as the current state.

## Gate C: implement and certify the REAL PostgreSQL importer and runtime selector

- Current postgres_synthetic_import.py uses invented local data and is **not** safe for private/Aiven import. A separate typed, reviewed, resumable or transactionally atomic importer is required; enforce target DB/host/schema pinning and sslmode=verify-full, least privileges, no fallback, explicit v18/v19 choice, source immutability, FTS/BM25 rebuild and privacy checks.
- Measure Aiven Free occupied/free disk and import overhead including PostgreSQL WAL, indexes and restore headroom; budget less than 20 total connections across Core, worker requests, maintenance and a reserved emergency session.
- Reconcile all relational rows and types, PK/FK/UNIQUE/CHECK, principal/owner/session/group privacy, memory search, epoch, conversations, dedupe, queues/voice, avatar and blobs. No private content, credentials, or per-user fingerprints in public logs.

## Gate D: backups, restore and rollback without PITR

- Create a PostgreSQL-consistent encrypted manual backup held outside the Aiven service. Restore and compare all rows/indexes using an independent disposable PG18 environment; Aiven Free's one disaster recovery backup is not a tested PITR plan.
- Preserve original Turso/SQLite and sidecar. After Aiven accepts writes, blindly falling back to an old Turso image could discard new messages; require write freeze and data reconciliation.
- Verify connection budget, retries, transaction interruption, boot/restart, index epoch fencing, voice cancellation and message ACK/deduplication.

## Gate E: staged service, then approved main release

1. After Gates A–D, create a production-safe selector plus explicit psycopg3 dependency in the Core Dockerfile and fail-closed target/data-readiness verification. **Do not reuse the preflight-only image for the Core.**
2. Validate isolated staging Core+Discord on real transport using invented identities: readiness, DMs, provider, older memory/BM25, private/group ACL, voice and interruption, media, edit/revoke, dedupe, restart and logs.
3. Freeze source consistently; take final snapshot; import; reconcile; verify encrypted backup and restore. Obtain explicit operator cutover approval.
4. Only then merge a release PR into main, deploy a pinned tested image manually, and review when CD may be re-enabled. Retain backup and old source for rollback; do not delete Turso.

## Certification checklist

- [x] Synthetic Run 1/2/3 integration on development branch
- [ ] Current Core and both Northflank jobs audited, image/command/CD and private mount verified
- [ ] Private PCS.4C pair intact, current and correctly retained
- [ ] Production importer and backend selector reviewed and tested
- [ ] Aiven Free capacity, TLS, ACL, row parity and connection budget proven
- [ ] Encrypted external backup plus independent restore and rollback tested
- [ ] Staging Core and Discord accepted with sanitized logs
- [ ] Pinned main release and operator cutover authorized

**Never represent an unchecked release gate as passed merely from synthetic CI. No production configuration is changed by this PR.**

Sources: https://northflank.com/docs/v1/api/project/services/get-service ; https://northflank.com/docs/v1/api/project/jobs/get-job ; https://northflank.com/docs/v1/application/run/override-command-entrypoint ; https://aiven.io/docs/products/postgresql/concepts/pg-free-tier ; https://aiven.io/pricing/postgresql

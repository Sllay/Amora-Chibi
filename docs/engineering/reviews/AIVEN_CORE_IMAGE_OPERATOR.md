# Staging Core PG18 — image build and operator handoff

## Candidate and safety

- Immutable code commit: `2bb2e37ecadcdd6f65ad0595fe324e643e313dd7`, merged via PR #308 into `feat/aiven-full-runtime-integration`.
- Image: `ghcr.io/sllay/amora-chibi-aiven-core:pg18-2bb2e37` (build metadata does **not** attest a deployment).
- This is the regular Core service image with pinned psycopg3 included. No real SQLite backup, customer records, Aiven tokens, Discord credentials, or secrets are baked into it.
- The image by itself is **not production cutover authorization**, even if CI passes.
- DO NOT change Northflank Core `loyal-coat` or Discord until a recovery backup with independent PG18 restore, staging smoke and source freshness gates are certified.

## Rollout contract (staging only, after gates)

- `AMORA_STORAGE_BACKEND=postgres-staging` plus literal independent approval `AMORA_PG_STAGING_APPROVAL=ALLOW_ONLY_ISOLATED_AIVEN_STAGING_CORE`; other providers still default to their prior explicit selection.
- Host, port and database pins from Aiven Console; mounted `AMORA_POSTGRES_CA_FILE` file, no inline PEM or fallback; DSN SSL verify-full.
- Only the isolated Aiven PG18 database with imported v18 schema and `structural_pass` is accepted by /health and /ready.
- Validate `AMORA_MODE=cloud`, durable avatars `AMORA_ASSET_STORAGE=database`, `AMORA_SECRET_LOCAL_API_TOKEN`, and remote-provider/Discord credentials via existing Northflank secrets, never via GitHub logs.
- Max 16 connections for the Core process under a 20-connection Aiven Free cap; reserve additional sessions for maintenance and workers.

## Acceptance

- Verify Core starts, /ready and /health return 200, and private memory from the historical data recalls only to the correct verified identity.
- Write a new non-sensitive test memory and message with operator approval, restart the Core, verify both can be retrieved.
- Validate Discord inbound/outbound/ACK/dedupe, provider generation, voice capture and TTS, privacy boundaries and reconnect.
- Ensure offsite backup + restore and PostgreSQL restart/rollback are tested before production cutover.

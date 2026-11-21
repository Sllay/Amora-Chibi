# Altare cutover — Amora Core + Discord Worker (2026-11)

**State:** staged engineering only. No production deployment, no credential export from Northflank, and no active second Discord Gateway have been authorized. Core Aiven PostgreSQL 18 stays authoritative. PG17 read model is a separate, future shadow feature.

## Why Docker Apps

The repository already has tested immutable images: root `Dockerfile` (Alpine Python 3.12 + psycopg) and `workers/discord/Dockerfile` (Node 22). Altare supports GHCR Docker images, linux/amd64 and persistent data folders; its GitHub-native App Hosting does not automatically reproduce the pinned Docker build. Prefer existing Docker Apps for parity.

## Step A: package source-controlled immutable images

Only an explicitly authorized owner can run `Actions → Altare - package reviewed release images` with confirmation `PACKAGE_ALTARE_STAGING`, or comment `/amora altare package` on issue #349 from account Sllay. That workflow builds and pushes images to GHCR with the exact main commit SHA (never `latest`) and does NOT deploy or connect Discord.

Image formats:
- `ghcr.io/sllay/amora-chibi-core:<MAIN_COMMIT_SHA>`
- `ghcr.io/sllay/amora-chibi-discord:<MAIN_COMMIT_SHA>`
- Optional browser worker `ghcr.io/sllay/amora-chibi-browser:<MAIN_COMMIT_SHA>` when explicitly requested (large Chromium image; normally unnecessary because Microlink is remote).

GHCR packages are potentially private even when repository source is accessible. The Altare Docker App must have a valid registry login if the image is private. Use a dedicated GitHub token with `read:packages`, never a chat-pasted token. Disable auto-update and pin SHA/digest for rollback.

## Step B: owner creates Altare Docker Apps

Altare team API tokens **cannot create apps**; owner must create them via console once. Under Altare → Apps → New app → Docker image:

1. `amora-core`: select Core image from GHCR, set port **8765** (Docker EXPOSE), data folder `/var/lib/amora` (must persist across deploy), appropriate RAM/CPU, and don't activate until secrets and Aiven certificate are installed. Core readiness endpoint is `/ready`; verify on the true HTTPS address.
2. `amora-discord`: select Discord image, no public TCP port necessary. **KEEP STOPPED** until Northflank's existing Discord Gateway is stopped at final cutover. One bot token must not be logged into two Gateway workers simultaneously.
3. Optional browser worker: only if Chromium sandbox, network rules, available disk/RAM and E2E security are confirmed. Core's remote reader and Microlink screenshot provider do not need it to run.

Altare free has a shared pool; check total image unpacked disk, app RAM and CPU. Browser Playwright may exceed small quotas; do not add it by default. Check actual available limits in console.

## Step C: transfer environment variables safely

Northflank uses both service runtime env and inherited secret groups. Service-level variables override inherited ones. The old host stores secret files too, which an env-only export CANNOT recreate.

**Recommended if using a mobile terminal (Termux) or a private computer:**

1. Export separately the effective runtime variables for the Northflank Core and Discord service using an authorized UI or the **GET** runtime environment endpoint with secrets-read permissions. Never send the export in this chat or upload it to GitHub. The existing `NF_BACKUP_API_TOKEN` was configured for logs; it may NOT have permission to read secrets.
2. Keep the exports outside the Git checkout, e.g. the Termux private home folder. Exported Northflank JSON can contain `data.runtimeEnvironment`; a native `.env` file also works.
3. Run `python tools/ops/altare_env_handoff.py --core /private/northflank-core.json --discord /private/northflank-discord.json --output-dir /private/altare-handoff`.
4. This local helper validates variable names, refuses missing key names, removes old-host settings and splits Core/Discord credentials. It creates `.env.altare-core` and `.env.altare-discord`, mode 0600, without showing secret values in stdout.
5. **Important:** Altare `altr env set <app> --file <file>` is documented for GitHub-native App Hosting. The docs do NOT promise this `--file` flag for Docker Apps. For our Docker Apps use Altare console **Settings → Image → Variables** or the documented `altr docker env set <app> KEY=VALUE` interface; verify the Docker CLI's current help before bulk import, and avoid terminal history with values. Never assume an undocumented bulk endpoint. A dedicated, reviewed API bridge can be added after token-based staging succeeds.
6. Reconfigure host-dependent names: Core `AMORA_PUBLIC_ORIGINS` must be its actual HTTPS origin. Discord `AMORA_CORE_URL` must point to that Core; DO NOT retain the Northflank Core URL. `PORT` is managed by the app configuration.
7. The Aiven PEM certificate is a separate **file**. Existing Core requires an absolute path from `AMORA_POSTGRES_CA_FILE`, pinned verified CA, and rejects `AMORA_POSTGRES_CA_PEM` for the production backend. Transfer the CA into an Altare persistent data folder, verify file presence and read permissions as user 10001, then point `AMORA_POSTGRES_CA_FILE` to its real mounted path. Do not assume a Northflank Secret File path remains valid at Altare.
8. Verify other secret *files* and provider credentials individually. Store shared provider keys only in Core, Discord Bot Token only in Discord, and the internal messaging token with the same value in both services. Do not copy NF backup/access tokens into Amora runtime.
9. Delete all exported/private temporary env files after verifying Altare settings. Do not put any secrets into PRs, logs, CI artifacts or the Git repository.

## Step D: staging and real cutover

1. Take and independently verify an Aiven PG18 backup and offsite restore; preserve Northflank configuration/snapshots for rollback.
2. Confirm Core Altare storage and Aiven TLS CA, health `/ready`, memory read/write, avatar assets, web navigation, screenshot diagnostics, and restart stability.
3. If the Core Docker App exists, a guarded manual action `Altare - guarded Core staging release` can deploy a **built image** only when GitHub Actions secret `ALTARE_TOKEN_DEPLOY` exists and variable `ALTARE_CORE_STAGING_READY=true` is explicitly enabled. It never touches the Discord worker and doesn't copy secrets.
4. Validate readiness and Discord-to-Core callback from a separate test if possible. Test real Microlink screenshots and actual Discord attachment objects. Monitor logs by read-only Altare token.
5. Only after 72-hour observation, owner approval and rollback plan: stop Northflank Discord worker, verify stopped; start Altare Discord worker; verify only one Gateway and ownership; then disable Northflank Core traffic. Do not delete Northflank volumes or original credentials prematurely.
6. If Altare fails, stop Altare Discord worker first, restore tested Northflank release and reconnect its single worker, continuing from Aiven PG18 memory. The profile switch in `core/amora/deployment_profiles.py` is release *intent*, not magic instant routing.

## Altare GitHub Actions access

- `ALTARE_TOKEN_READONLY`: Altare **Read only** token plus `servers:console` if fetching logs. The workflow reports only allowed event codes, counts and app availability; no raw messages or credential values.
- `ALTARE_TOKEN_DEPLOY`: separate **Deploy from CI** token restricted to the created Core app, with `servers:view` and `servers:power` scopes. No application creation allowed via team token.
- Set GitHub Actions variable `ALTARE_CORE_STAGING_READY=true` only after verified Aiven file mount, host variables, quota and backup. Staging deploy still needs explicit `workflow_dispatch` confirmation.
- Create the required GitHub Environment `altare-staging`; add reviewers when available. Disable auto-update of Docker Apps during migration.

## Known blockers before declaring the migration done

- No Altare account/team API connection has been authorized to this conversation yet.
- No authorized transfer of the actual Northflank secret values or mounted CA file has occurred.
- Neither immutable GHCR image nor the target Docker Apps have been verified live in this environment.
- Aiven production TLS and data durability, single Discord Gateway and 72-hour uptime require true end-to-end validation.
- PG17 fast-read projection/outbox is a distinct future feature and must NOT block relocation of Core, Discord and web processes from Northflank to Altare.

Official docs: https://docs.altare.gg/apps/docker , https://docs.altare.gg/cli/reference , https://docs.altare.gg/altare-api/tokens , https://northflank.com/docs/v1/api/project/services/get-service-runtime-environment
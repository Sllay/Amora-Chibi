# Altare migration: Core/Discord/Jobs/Storage portability audit

Status: **STAGING PREPARATION, NOT LIVE DEPLOYED.** Source-controlled profile is still `northflank`; do not switch it until runtime readiness, backup/restore, ownership fencing and 72h observations are verified.

## What moves and what does not

| Dependency | Found in source | Altare target / gate |
|---|---|---|
| Core container | root `Dockerfile`, UID 10001, `amora serve` | Build immutable `linux/amd64` image, Altare Docker App on port **8765**, HTTPS ingress; `/ready` and `/live` |
| Discord transport | `workers/discord/Dockerfile`, `workers/common/core-client.mjs` | Separate Docker App with **no exposed port**. Set `AMORA_CORE_URL=https://<actual-altare-core-domain>`; same `AMORA_MESSAGING_SERVICE_TOKEN` in Core and Worker |
| Local files | `RuntimePaths.from_environment` | `AMORA_HOME=/var/lib/amora` and Docker App Data folder **also `/var/lib/amora`**. Altare Docker Apps preserve only the configured data folder across deploys. Verify non-root UID 10001 can write there. Never assume old Northflank volume paths exist |
| Database authority | guarded `aiven_production_core_backend.py` | `AMORA_STORAGE_BACKEND=postgres`; preserve full Aiven PG18 endpoint pins and operator approval, no database import/cutover in this host migration |
| Aiven CA certificate | `_tls_pinned_aiven_backend` | It **requires a real readable PEM file**, `AMORA_POSTGRES_CA_FILE`, and explicitly rejects inline `AMORA_POSTGRES_CA_PEM`. Place the independently verified CA via Altare **Files** in the persistent data folder, e.g. `/var/lib/amora/secrets/aiven-ca.pem`; verify permissions and TLS; never copy the former Northflank file path |
| Avatar assets | `ServerSettings.validate_persistence` | Prefer `AMORA_ASSET_STORAGE=database`, or certify a durable data folder with `AMORA_DURABLE_FILESYSTEM=true`. App can still need writable cache, logs and secrets |
| Web browser | `amora.web_tools.browser_client` / `workers/browser/Dockerfile` | Optional, separate large Chromium image; no local Chromium in Core. New `AMORA_WEB_BROWSER_URL` and `AMORA_WEB_BROWSER_TOKEN` only if deployed. Never blindly carry `AMORA_WEB_BROWSER_PRIVATE_HTTP=1` from old network |
| Operator PCS4 recovery | `storage/pcs4_dev.py` | Northflank-only API. **Disabled on Altare** even if old env was mistakenly copied. Replace with separately certified Altare job APIs rather than trying to reuse an NF run ID |
| GitHub Northflank encrypted logs | `.github/workflows/northflank-encrypted-archive.yml` | Collector/diagnostic are gated on internal selected host and stop automatically after profile cutover; keep their ciphertext history for incident retention |
| Internal Aiven jobs | `deploy/Dockerfile.aiven-*` and relevant GitHub Actions | Separate one-off maintenance tools. DO NOT carry their Northflank job IDs or startup commands into the serving Core; review every job before moving it to an Altare Command Job |
| Public security configuration | `SecuritySettings`, `AMORA_PUBLIC_ORIGINS` | Update to the actual Altare HTTPS Core origin. Do not transfer `AMORA_TRUSTED_PROXY_IPS`; only set known IP peers after verifying the new proxy route |
| Local private config | `SecretStore` and `identity_bindings.json` | `AMORA_SECRET_LOCAL_API_TOKEN` must remain stable; check whether extra vault files or private identity bindings exist **on the old volume** and transfer separately by encrypted channel, only if not already durably in Aiven |
| Platform presence | Discord Gateway connects to Aiven-shared desired state | Start **only one production Discord Worker**. Keep the Altare worker stopped or without its token until the old worker is fenced and the handoff is authorized. Duplicate gateway processes can interfere even when Discord returns READY |

## Altare configuration

Choose **Docker Apps**, not an arbitrary PG17 connection with the old Core adapter. Docker Apps are documented at https://docs.altare.gg/apps/docker. Images must provide linux/amd64, must fit the team's available disk; an optional Chromium worker costs substantial disk/memory. GitHub Actions can package immutable Core and Discord images from existing Dockerfiles. A private GHCR repo requires Altare's registry login; never store that login in Git or build arguments.

1. Create an Altare Docker App `amora-core` using the immutable Core image (start command left at `amora serve`), service port **8765**, data folder `/var/lib/amora`, desired CPU/RAM, auto-updates OFF.
2. Configure *Core only* production variables: `AMORA_MODE=cloud`, `AMORA_HOST=0.0.0.0`, `AMORA_HOME=/var/lib/amora`, `AMORA_STORAGE_BACKEND=postgres`, `AMORA_ASSET_STORAGE=database`, `AMORA_PG_PRODUCTION_APPROVAL=AUTHORIZE_VERIFIED_AIVEN_PG18_CORE_CUTOVER`, verified `AMORA_POSTGRES_DSN`, `AMORA_STAGING_EXPECTED_HOST`, `AMORA_STAGING_EXPECTED_PORT`, `AMORA_STAGING_EXPECTED_DATABASE`, `AMORA_POSTGRES_CA_FILE=/var/lib/amora/secrets/aiven-ca.pem`, `AMORA_SECRET_LOCAL_API_TOKEN`, `AMORA_MESSAGING_SERVICE_TOKEN` and `AMORA_PUBLIC_ORIGINS=https://<real-core-domain>`.
3. Via the Altare File Manager or authorized Files API, transfer only the certificate chain previously verified against the Aiven instance into the persistent data folder. Check permissions for UID 10001. App may fail to start until this file exists; never bypass TLS to hide this prerequisite.
4. Use `python -m amora.host_preflight` inside a prospective *Core* container to see fixed reason codes before enabling service traffic. If path/file checks cannot be performed before first boot, do a restricted staging check and observe `/ready` and PostgreSQL SSL verification after loading the CA.
5. Create Altare Docker App `amora-discord` from its immutable image. No public port. Set only Discord/application variables, `AMORA_MESSAGING_SERVICE_TOKEN` and `AMORA_CORE_URL=https://<real-core-domain>`. Keep it STOPPED until Core readiness and single-gateway cutover. The same service token must match Core byte-for-byte; do not expose Aiven DSN to this worker.
6. Optional standalone `amora-web` browser app: require measured RAM/disk and a reviewed HTTPS private interservice route; may remain disabled if hosted Microlink/Jina are sufficient. The app needs `AMORA_WEB_BROWSER_TOKEN` on both ends but never the Discord bot token or Aiven DSN.
7. Review old scheduled jobs one by one. Native Altare Command Jobs support bounded command runs inside an app; do not deploy legacy Northflank-specific API scripts as-is. `https://docs.altare.gg/apps/jobs`.

## Exporting environment variables without leaking secrets

Export runtime variables **separately for the Core and Discord Worker** in Northflank, either JSON or `.env`. Northflank source secrets may not be recoverable by a read-only token: if the console does not reveal a value, re-enter/rotate it privately. Do NOT post any export or `.env` to GitHub, ChatGPT, issue comments, GitHub Actions logs or screenshots.

Use the local offline utility `python tools/ops/altare_env_handoff.py --core /PRIVATE/core.json --discord /PRIVATE/discord.env --output-dir /PRIVATE/altare-output`. It strips host-specific values, old PCS4/NF tokens, old `AMORA_HOME`, proxy configuration and stale Core URL. It returns new private `.env.altare-core` and `.env.altare-discord` files with mode 0600; does not transfer them.

For newly created **Docker Apps**, the documented CLI supports `altr docker create name image --env-file file` **at creation**, or use the console app variables / `altr docker env set name KEY=value` for an existing app. The `altr env set name --file` command is documented for **App Hosting**, not automatically for the Docker App subcommand. Always check CLI help before bulk import. `https://docs.altare.gg/cli/reference`.

Some target-specific values are deliberately NOT exported, and must be configured after the actual Altare URLs and storage location are known:

- `AMORA_CORE_URL`, `AMORA_PUBLIC_ORIGINS` — new HTTPS address.
- `AMORA_HOME`, `AMORA_POSTGRES_CA_FILE`, `AMORA_DURABLE_FILESYSTEM`, `AMORA_ASSET_STORAGE` — real Altare data-folder mapping and existing backend choice.
- `AMORA_TRUSTED_PROXY_IPS` — do not copy old proxy trust ranges.
- `AMORA_WEB_BROWSER_URL`, `AMORA_WEB_BROWSER_PRIVATE_HTTP` — only after evaluating networking.
- Northflank-only recovery job settings are omitted completely.

GitHub Actions has `altare-readonly-diagnostics` to inspect existing apps and count only allowlisted log error codes using a **read-only** Altare API token. A separate image packaging workflow can produce fixed, immutable GHCR image tags. Until an authorized Altare token is provided via Actions Secrets and the apps exist, those checks cannot access the account.

## Release/cutover checklist

- [ ] Both PR #347 (web history) and #348 (host profiles) are merged and the chosen release SHA has passed tests.
- [ ] No unverified secret file / path assumptions; UID 10001 can write `/var/lib/amora`; volume actually survives redeploy.
- [ ] Core responds from HTTPS `/ready`; Aiven PG18 is pinned with verify-full and latest memory visible; no schema migration attempted.
- [ ] Validate local filesystem-dependent config/identity and avatar assets after Core restart.
- [ ] Discord Worker still stopped; Core's desired presence state must not connect an overlapping gateway.
- [ ] Optional browser worker security/network and log retention configured.
- [ ] Read-only Altare token connected to ChatGPT via approved plugin or GitHub CI; no token supplied in chat.
- [ ] One production Discord gateway is deliberately transferred, once, with identity/role/response/attachment tests.
- [ ] 72h sustained restarts, RAM/CPU, provider/TLS, Discord and memory consistency observed before retiring Northflank.
- [ ] Rollback means restore tested Northflank release against **the same Aiven**, and keep the Altare Discord gateway disabled, not two-way replication.

**Important:** A source-controlled profile change alone does NOT provision or migrate a service, move data, grant permissions, install certs or perform rollback. It is only a reviewed desired deployment choice. The real host must pass the preflight and operator approval.

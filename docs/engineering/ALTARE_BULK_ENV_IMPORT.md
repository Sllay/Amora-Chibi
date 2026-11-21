# Altare Docker Apps — bulk encrypted env import from iPhone

NEVER commit an exported .env or JSON to Git, even to a private repo. Use GitHub Actions Secrets.

GitHub repository: Sllay/Amora-Chibi -> Settings -> Secrets and variables -> Actions -> New repository secret.

Save three secrets by pasting their entire values once:
- AMORA_CORE_ENV_BUNDLE : the complete exported Core .env or JSON
- AMORA_DISCORD_ENV_BUNDLE : the complete exported Discord .env or JSON
- ALTARE_TOKEN_CONFIG : an Altare Team API token scoped only to changing variables for Core-Amora and Discord-Amora

Run Actions -> Altare - import encrypted env bundle to stopped Docker App, from the main branch, signed in as Sllay.
Select core or discord and provide the ACTUAL HTTPS Core URL (e.g. https://core-amora.go.altare.global if that is its assigned name).
First select mode=check. This validates the bundle and only prints the number of variables or missing variable NAMES.
Stop the target Docker App before any mode=apply. Choose mode=apply and type APPLY_CORE_STOPPED for Core, or APPLY_DISCORD_STOPPED for Discord.
Discord-Amora must remain STOPPED until the Northflank Discord Gateway has been stopped and the single-gateway cutover explicitly authorized. Applying vars to a running app can restart it.

Security: the workflow runs only on main, only for Sllay; never send secrets as workflow inputs, issues, or comments. It does not start apps.
Core gets new AMORA_PUBLIC_ORIGINS, AMORA_MODE=cloud, AMORA_STORAGE_BACKEND=postgres, AMORA_ASSET_STORAGE=database, and AMORA_POSTGRES_CA_FILE=/var/lib/amora/secrets/aiven-ca.pem.
Discord gets the actual AMORA_CORE_URL and only allowlisted worker variables. Northflank-only tokens and paths are removed automatically.
The workflow DOES NOT create the Aiven certificate or approve a production DB migration. The verified CA PEM file must still be uploaded to /var/lib/amora/secrets/aiven-ca.pem and be readable by UID 10001.
The PG18 data, restore, endpoint pins, authorization and HTTPS /ready must be independently checked before enabling Core. Never activate two Discord Gateways.

Altare Docker Apps documentation only confirms altr docker env set for an EXISTING Docker App, not an --env-file option. The workflow therefore sets validated keys individually.
An interrupted import may leave a partial set; rerun while the app is stopped. The importer does not print secret values or the CLI output.
Limits: Altare <= 200 env vars and <= 32KB per app; GitHub Actions secrets <= 48KB each. No support for multiline secret values; use mounted secret files instead.

Docs: https://docs.altare.gg/apps/docker
Docs: https://docs.altare.gg/cli/reference
Docs: https://docs.github.com/en/actions/reference/security/secrets

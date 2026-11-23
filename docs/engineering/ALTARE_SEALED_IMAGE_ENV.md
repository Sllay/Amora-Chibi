# Altare — encrypted Core/Discord environments inside GHCR images

**Purpose:** Deploy to existing Altare Docker Apps without `altr login` and
without importing 44 separate variables through the blocked Altare API.
This is an opt-in staging packaging workflow. It never deploys or starts an app.

## Security design

- Two independently generated **X25519** key pairs. GitHub receives ONLY
  the public keys, as **GitHub Actions Variables**, not private keys.
- Existing GitHub Actions secrets `AMORA_CORE_ENV_BUNDLE` and
  `AMORA_DISCORD_ENV_BUNDLE` are parsed independently and filtered through
  the already audited Altare env planner. Do not upload a plain `.env`
  into Git, an issue, or GHCR.
- Sealing uses a fresh ephemeral X25519 key, 16-byte random salt,
  HKDF-SHA256, random 12-byte nonce, and AES-256-GCM authenticated encryption.
  Core and Discord use different keys and context-bound AEAD AAD.
- The GitHub runner writes **only encrypted output** in its private temp dir.
  Docker BuildKit `secret-files` passes that ciphertext to the image layer;
  no raw `.env` file, private key, or build argument contains credentials.
- Each Altare app holds exactly one runtime variable,
  `AMORA_IMAGE_ENV_PRIVATE_KEY`, containing its own private key in
  base64-encoded PKCS8 DER. Startup decrypts **in memory**, removes the key
  from the environment passed to the application, and starts normally.
  Missing or invalid keys cause startup failure, not a fallback to a partial
  environment.
- The envelope cannot be opened using only its public key. If the private
  key is exposed, rotate it and rebuild the corresponding GHCR image.

## 1. Generate keys locally (Termux; no Altare CLI login)

At the repository root after `git pull --ff-only`:

```bash
pkg install openssl -y
python -m tools.ops.altare_sealed_image_keygen
```

The generator writes four files **only** in private Termux HOME:

```text
~/.config/amora/altare-image-keys/core-public.b64
~/.config/amora/altare-image-keys/core-private.b64
~/.config/amora/altare-image-keys/discord-public.b64
~/.config/amora/altare-image-keys/discord-private.b64
```

Private keys and public keys are generated as two independent pairs.
The script refuses to overwrite keys, and it does not print their values.

## 2. Configure the two public keys in GitHub

Repository `Sllay/Amora-Chibi` -> **Settings -> Secrets and variables ->
Actions -> Variables** (NOT Actions Secrets needed for public keys):

- `AMORA_IMAGE_CORE_PUBLIC_KEY`: exact one-line contents of `core-public.b64`
- `AMORA_IMAGE_DISCORD_PUBLIC_KEY`: contents of `discord-public.b64`

The GitHub repo already had `AMORA_CORE_ENV_BUNDLE` and
`AMORA_DISCORD_ENV_BUNDLE` as **Actions secrets** in the previous setup.
Validate that they contain the latest version of the phone's
`Download/core/.env` and `Download/discord/.env` respectively. The
GitHub Actions packaging prints **only variable counts**; compare with the
local importer validation (previous result: 37 Core, 7 Discord). Do not use
a mismatched/stale source. Changing these secrets is done via the GitHub
Secret UI; no Git commit or chat is appropriate.

## 3. Set the two private keys in Altare browser console

**Keep both Altare Docker Apps stopped during setup. The existing Northflank
Discord worker must remain the only active Discord Gateway.**

In `Core-Amora -> Settings -> Image -> Variables`, add:

```text
AMORA_IMAGE_ENV_PRIVATE_KEY=<contents of core-private.b64>
```

In `Discord-Amora -> Settings -> Image -> Variables`, add
the **same variable NAME**, but with contents of `discord-private.b64`.

These private values are NOT interchangeable. Never paste a private key
into GitHub Actions, source code, the image tag, comments or this chat.
Altare stores Docker App variables encrypted. Do not enable the Discord app.

For a private GHCR image, configure Altare's **Private registry** login once
using a GitHub account and read-only package token (`read:packages`);
this registry token is distinct from the Altare API token.

## 4. Build the two encrypted GHCR images, without deploying

GitHub -> Actions -> **Altare - package sealed Core and Discord images** ->
Run workflow (branch `main`).

Input:
- `confirmation` = `PACKAGE_SEALED_ALTARE`
- `core_origin` = the exact assigned Core HTTPS origin (normally
  `https://core-amora.go.altare.global`; check the actual Altare panel)

The workflow checks both secret bundles, shared internal token consistency,
Core admin token length, and both public keys. It then publishes:

```text
ghcr.io/sllay/amora-chibi-core:sealed-<MAIN_COMMIT_SHA>
ghcr.io/sllay/amora-chibi-discord:sealed-<MAIN_COMMIT_SHA>
```

These are pinned, immutable version identifiers. Check the count printed
by the preflight against the phone's local `ALTARE_LOCAL_ENV_CHECK_OK`
output. If they differ, DO NOT DEPLOY.

No CLI login, Altare REST call, deployment or Discord Gateway start happens
in this GitHub workflow.

## 5. Point the stopped Docker Apps at their images

In Altare's browser Settings -> Image, set the Core image to the sealed
Core tag and the Discord image to the sealed Discord tag, retaining the
two private-key variables. Leave **Update automatically disabled** until
staging passes. A changed image can deploy/restart an app; do not change
the Discord image on a running second Gateway.

**Unresolved preflight:** The Core's Aiven pinned CA certificate must exist
at `/var/lib/amora/secrets/aiven-ca.pem` in the **persistent data folder**,
readable by UID 10001. The prepared Core env references this file, but the
encrypted image cannot conjure it automatically. Validate real Aiven TLS,
PG18 state and `/ready` before promoting traffic. On the Discord side,
do not activate until Northflank's Gateway is explicitly stopped.

## Implementation and rollback

The standard unsealed GHCR workflow still works. The existing Docker
images now have a startup wrapper that is a no-op without a sealed payload
or a private key. A sealed image will **fail closed** if no correct runtime
private key is present. Backups of private keys must be stored separately
from public source control; a lost private key makes that image's payload
undecryptable until a new image is built with a new key pair.

Sources: https://docs.altare.gg/apps/docker and
https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images

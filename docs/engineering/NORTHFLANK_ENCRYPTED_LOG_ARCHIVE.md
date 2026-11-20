# Amora Northflank encrypted log archive

The source repository is PUBLIC. Only **AES-256 encrypted** ZIP bytes belong in `ops/logs/northflank-latest.zip.gpg`. Never commit unencrypted logs, exported secrets, CI artifacts, dump files, keys, or GPG passphrases.

## Design

**Collection workflow:** `Northflank encrypted log archive` (daily UTC or manual). It uses Northflank's read-only API to enumerate every service and job, collect up to the six newest service deployments / job runs available, and retain up to 600 last runtime lines per version. If provider retention no longer contains an older version, an empty/error marker replaces the unavailable log. The script creates folders by resource, writes index JSON for each, packages the whole tree into a ZIP in a temporary GitHub-hosted runner directory, encrypts that ZIP using GPG AES256/SHA512, commits only `ops/logs/northflank-latest.zip.gpg`, and deletes temporary files. No plaintext artifacts are uploaded.

**Diagnostic workflow:** `Northflank encrypted log diagnosis` (runs after a successful collection, or manually). It decrypts the ZIP only within a disposable runner, scans ONLY predefined event names and reason codes, and commits a JSON of counts in `ops/logs/diagnostics.json`. No original log line, URL, user content, request/response body, database content, secret, or arbitrary regex match is printed or committed. A person who has the GPG key may recover the full archive privately; the connected GitHub app can read only the safe diagnostic JSON.

Do not claim that ChatGPT can read the full encrypted ZIP directly. For richer support, expand an explicit allowlist of diagnostic event codes in source, review and test it, then regenerate the report.

## Required one-time GitHub configuration

In the GitHub repository, open **Settings → Secrets and variables → Actions**.

- Add a **Repository secret** `NORTHFLANK_API_TOKEN` with a dedicated **read-only** Northflank API token scoped to the project. Grant service/job listing, history and observability log *read* only, and avoid secret/environment read/write permissions.
- Add a **Repository secret** `AMORA_LOG_ARCHIVE_PASSPHRASE` containing a long random passphrase (ideally 32+ bytes). Never add the passphrase to git, a variable, a chat message or a file.
- Add a **Repository variable** `NORTHFLANK_PROJECT_ID` with the project ID.
- If the Northflank token is team/org-scoped, add a **Repository variable** `NORTHFLANK_TEAM_ID` with its API team ID. Leave unset for a project/user-scoped token.

The Northflank key already used by the runtime service is NOT implicitly available to GitHub Actions; copy or create a scoped token separately. The Actions workflows will fail cleanly with a missing-configuration label until configured.

Run **Actions → Northflank encrypted log archive → Run workflow**, then check the encrypted ZIP is present. The second workflow runs automatically afterward; its code-only report appears at `ops/logs/diagnostics.json`. Run the diagnostic workflow manually at any time to reprocess the last encrypted snapshot without contacting Northflank.

**Provider limits:** historical Northflank logs may be unavailable. Six snapshots means up to six historical deployment/run IDs per resource, not a guarantee of six complete records. Logs are bounded excerpts, and any unavailable version must be marked rather than fabricated.

## Security notes and operational limits

- The GPG key must remain in GitHub Actions **Secrets**, not the repository. A normal password-protected ZIP (ZipCrypto) is weaker; `.zip.gpg` encrypts the *entire* ZIP including member names.
- Raw logs contain private data. The collector applies conservative redaction before encryption, but that is not a security guarantee. Restrict contributors' ability to change workflows, protect `main`, rotate the Northflank token and archive passphrase if exposed.
- GitHub-hosted runners temporarily store plaintext while processing it and are then discarded; strictly zero transient storage is impossible. This system creates **no plaintext Git commit or uploaded CI artifact** and never echoes log content.
- Git keeps **past ciphertext commits** even though the current ZIP contains only six versions. Rotating the password doesn't re-encrypt earlier Git history. For strong retention enforcement or high sensitivity, use a separate PRIVATE archive repository with restricted actions instead.
- Public diagnostic counters are deliberate and limited to predefined event names. If even these metadata are sensitive, disable the diagnostic workflow and store the archive in a private repository.
- GitHub API integration available to ChatGPT can read the public code-only diagnostic JSON. It cannot inject GitHub Action secrets, decrypt the ZIP locally or independently trigger a new workflow in the current connector.

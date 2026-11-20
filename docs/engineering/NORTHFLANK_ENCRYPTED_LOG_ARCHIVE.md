# Amora Northflank encrypted log archive

The source repository is PUBLIC. Only **AES-256 encrypted** ZIP bytes belong in `ops/logs/northflank-latest.zip.gpg` **on the dedicated branch `ops/northflank-log-archive`**. Never commit unencrypted logs, exported secrets, CI artifacts, dump files, keys, or GPG passphrases.

## Design

**Collection workflow:** `Northflank encrypted log archive` (daily UTC or manual). It uses Northflank's read-only API to enumerate every service and job, collect up to the six newest service deployments / job runs available, and retain up to 600 last runtime lines per version. If provider retention no longer contains an older version, an empty/error marker replaces the unavailable log. The script creates folders by resource, writes index JSON for each, packages the whole tree into a ZIP in a temporary GitHub-hosted runner directory, encrypts that ZIP using GPG AES256/SHA512, commits only `ops/logs/northflank-latest.zip.gpg` on `ops/northflank-log-archive`, and deletes temporary files. No plaintext artifacts are uploaded.

**Diagnostic workflow:** `Northflank encrypted log diagnosis` (runs after a successful collection, or manually). It decrypts the ZIP only within a disposable runner, scans ONLY predefined event names and reason codes, and commits a JSON of counts in `ops/logs/diagnostics.json` on the same archive branch. No original log line, URL, user content, request/response body, database content, secret, or arbitrary regex match is printed or committed. A person who has the GPG key may recover the full archive privately; the connected GitHub app can read only the safe diagnostic JSON.

Do not claim that ChatGPT can read the full encrypted ZIP directly. For richer support, expand an explicit allowlist of diagnostic event codes in source, review and test it, then regenerate the report.

## Existing GitHub configuration (Sllay/Amora-Chibi)

The workflow now reuses the existing **Actions Secrets** visible in the repository:

- `NF_BACKUP_API_TOKEN`: forwarded to the collector process as `NORTHFLANK_API_TOKEN`, used exclusively for authenticated **GET** calls. No new token is required. The existing backup token may have broader permissions; for least privilege consider a separate observability read-only token when practical. It must have service/job listing, run/deployment history and log viewing rights.
- `AMORA_LOG_ARCHIVE_PASSPHRASE`: used for AES-256 ZIP encryption/decryption. The workflow requires **at least 32 characters**; GitHub never exposes a stored secret's value for verification, so only a real workflow run can confirm this check.
- Project: `default` (built-in fallback, override optionally via the Actions variable `NORTHFLANK_PROJECT_ID`).
- Team: `mejorberrys-team` (known project team from the existing read-only audit tool; override optionally via the Actions variable `NORTHFLANK_TEAM_ID`).

**No new variable is required for the current Northflank project.** The key values are never copied into workflows, log files or diagnostics. If a Northflank 403 occurs, check whether the existing backup token includes *View Observability* access rather than expanding the collector to a write API.

Run **Actions → Northflank encrypted log archive → Run workflow**, then check the encrypted ZIP exists on the archive branch. The second workflow runs automatically afterward; its code-only report appears at `ops/logs/diagnostics.json` on the archive branch. Run the diagnostic workflow manually at any time to reprocess the last encrypted snapshot without contacting Northflank.

**ChatGPT-triggered collection:** commenting `/amora logs collect` on the owner-only control issue [#342](https://github.com/Sllay/Amora-Chibi/issues/342) triggers the archive workflow on the trusted default branch. Commenting `/amora logs diagnose` triggers only the decrypt-and-diagnose workflow. The GitHub connector can create these comments as `Sllay` without changing `main` or asking for a token. Both workflows compare the issue number, exact message body and author login; no comment content is executed as shell. Routine collection happens on schedule, and diagnostics follow it automatically.

**Accessing reports:** use the connected GitHub app to read `ops/logs/diagnostics.json` **with ref `ops/northflank-log-archive`**; `main` contains the code and documentation only. Data updates never make commits to `main` and cannot trigger Northflank deployments from source-code commits.

**Provider limits:** historical Northflank logs may be unavailable. Six snapshots means up to six historical deployment/run IDs per resource, not a guarantee of six complete records. Logs are bounded excerpts, and any unavailable version must be marked rather than fabricated.

## Security notes and operational limits

- The GPG key must remain in GitHub Actions **Secrets**, not the repository. A normal password-protected ZIP (ZipCrypto) is weaker; `.zip.gpg` encrypts the *entire* ZIP including member names.
- Raw logs contain private data. The collector applies conservative redaction before encryption, but that is not a security guarantee. Restrict contributors' ability to change workflows, protect `main`, rotate the Northflank token and archive passphrase if exposed.
- GitHub-hosted runners temporarily store plaintext while processing it and are then discarded; strictly zero transient storage is impossible. This system creates **no plaintext Git commit or uploaded CI artifact** and never echoes log content.
- Git keeps **past ciphertext commits** even though the current ZIP contains only six versions. Rotating the password doesn't re-encrypt earlier Git history. For strong retention enforcement or high sensitivity, use a separate PRIVATE archive repository with restricted actions instead.
- Public diagnostic counters are deliberate and limited to predefined event names. If even these metadata are sensitive, disable the diagnostic workflow and store the archive in a private repository.
- GitHub API integration available to ChatGPT can read the public code-only diagnostic JSON. It cannot inject GitHub Action secrets, decrypt the ZIP locally or independently trigger a new workflow in the current connector.

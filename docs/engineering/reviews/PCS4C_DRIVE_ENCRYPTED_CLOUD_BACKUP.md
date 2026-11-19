# PCS.4C encrypted off-provider backup — browser-only authorization (manual Job)

Status: code candidate, only synthetic/offline CI. No Google OAuth session,
Drive upload or real Northflank Job run has been performed during development.

## Goal and strict safety requirements

One manual invocation in the existing `pcs4c-recovery` Northflank Job:
original `/private/pcs4c/backup/amora-pre-pcs4c.db` and its
`.avatar.zip` sidecar remain in-place on the existing Single Read/Write
volume; no new Northflank Job or volume transfer is necessary.

- Default is OFFLINE read-only inspect. A separate explicit confirmation is
  required for outbound cloud upload; this never sends anything to Aiven.
- `PCS4C` source + state.json hashes and exact v18 schema are checked before
  transfer using the existing pinned private snapshot audit.
- One encrypted .agcm container holds only these TWO exact files:
  gzip tar stream encrypted with AES-256-GCM and scrypt KDF, unique salt/nonce
  from operating-system CSPRNG, no plaintext temporary archive while writing.
  The passphrase must be at least 20 characters; use a **random** long secret.
- Only ciphertext is written in the temporary Job filesystem or sent to Drive;
  plaintext restored for verification is temporary, outside `/private`.
- Google OAuth access token is only in Job process memory; no refresh token,
  persistent `rclone.conf`, identity or login key is put in the GitHub repo
  or `/private`. No private plaintext or checksum in logs.
- Google OAuth uses the official TV/limited-input **device-code flow**,
  `https://www.google.com/device`, with narrowly scoped `drive.file` access.
  User authorizes the *existing running Job* in their own browser.
- A new Google Drive folder is created and an encrypted upload is confirmed
  with server size and MD5. The Job downloads that exact ciphertext back,
  checks file bytes, authenticates the AEAD tag, unpacks two exact members,
  checks SHA-256 for both and runs SQLite v18 integrity verification on the
  recovered pair. Only THEN reports backup success.
- All errors produce only predefined codes, without revealing secrets. No
  dangerous retry after ambiguous upload. Independent off-provider manual
  restore is still advisable after Google Drive verification.

## One-time Google Cloud Console setup (browser on any phone or computer)

The user does **not** need Android or Termux software or rclone. Google requires
creating **their own OAuth client** because generic shared client IDs have
been retired/are being retired and this code intentionally requests a
separate limited-scope client for this personal backup.

1. Visit https://console.cloud.google.com/ with the Google account that
   will own the OAuth project, select/create a project (e.g. `Amora Backup`).
   Avoid paid subscriptions/upgrades; normal Drive API small-volume usage
   should stay inside free API quotas, but verify Console billing prompts.
2. Open **APIs & Services -> Library**, find **Google Drive API**, enable it.
3. In **Google Auth Platform / OAuth consent**, register an app name such as
   `Amora Private Backup`, Audience **External** for ordinary Gmail, and
   add *your own Google email* as a **Test user** if app is in Testing mode.
4. In **Data Access / Scopes**, enable ONLY
   `https://www.googleapis.com/auth/drive.file`. Do NOT enable broad
   `.../auth/drive` scope. This restricts application access to app-owned
   files such as the newly created backup folder and archive.
5. In **Clients -> Create Client**, choose **TVs and Limited Input devices**,
   name `Amora Manual Job`. Copy the **Client ID** string ending in
   `.apps.googleusercontent.com`. Do not send secrets or screenshots of
   credentials in chat. No OAuth client secret is necessary for this
   public-device flow.

Google docs: https://developers.google.com/identity/protocols/oauth2/limited-input-device
Drive scopes: https://developers.google.com/workspace/drive/api/guides/api-specific-auth

## Northflank configuration (existing recovery Job only)

Before changing Dockerfile, record original `pcs4c-recovery` Job settings and
working image revision, persistent volume mount `/private`, secrets and
default command for easy restoration. Never detach or move the original volume.

Temporarily set this same Job's build from repository
`Sllay/Amora-Chibi`, branch `feat/aiven-full-runtime-integration` at
a validated commit, Dockerfile location:

    /deploy/Dockerfile.aiven-private-pcs4c-bridge

Build manually, no continuous deployment, schedules, cron or concurrent
runs. The default Docker command remains the original safe bridge inspector
that checks source offline. The image includes pinned `cryptography==46.0.4`.
The new backup command is present in that same image but is not automatic.

Set exactly two **runtime environment** variables (mark as secure, not build
args or public endpoints):

    AMORA_GOOGLE_DRIVE_OAUTH_CLIENT_ID = <copy your Google OAuth TV client ID>
    AMORA_BACKUP_PASSPHRASE = <strong independent random passphrase>

Record the passphrase in an off-Northflank password manager; **DO NOT share it
with ChatGPT, put it in GitHub, paste it in log screenshots, or save only in
Northflank**. Losing it means permanent loss of the off-provider backup.

Optional safe first run, with Custom Command (without changing CMD default):

    python -m amora.storage.pcs4c_drive_backup inspect

Expect `PCS4C_DRIVE_BACKUP {"status":"pcs4c_backup_ready_for_encryption",...}`.
No Internet connection or private volume modifications are made.

When operator explicitly approves Drive upload, run a ONE-SHOT manual Job
with CMD override Custom command:

    python -m amora.storage.pcs4c_drive_backup backup --confirm BACKUP_ENCRYPTED_PCS4C_TO_MY_GOOGLE_DRIVE

**Watch Job logs immediately**: after encryption the Job outputs e.g.

    PCS4C_DRIVE_AUTHORIZE {"status":"waiting_for_google_consent","verification_url":"https://www.google.com/device","user_code":"XXXX-XXXX",...}

1. Open https://www.google.com/device in any browser (phone is fine).
2. Sign in to the Google account whose Drive should contain backup.
3. Enter **the ephemeral code from the logs**, inspect `drive.file`
   requested permission, then allow. The code is time-limited and only
   applies to this particular running Job. Never input account password
   into the Northflank console.
4. Keep the Job run active. It polls Google until permission is granted or
   the code expires. No need to paste an OAuth token into ChatGPT.
5. On success the ONLY final certificate is:
   `PCS4C_DRIVE_BACKUP {"status":"encrypted_backup_uploaded_and_restored",
   "remote_ciphertext_download_verified":true,
   "sqlite_restore_from_drive_verified":true,...}`
6. Open Drive and inspect the new private
   `Amora Backups Criptografados` folder. Its `.agcm` file is **not a
   regular .7z or .zip**; it can be recovered using this repository's
   decrypting recovery command and your saved passphrase.

On failure, save only the sanitized `reason` and review before rerun;
an orphan partial folder/file can remain in Drive. Never expose any OAuth
session, passphrase, plaintext dump or raw HTTP response.

## Independent offline recovery

With a trusted Python environment having the `cryptography==46.0.4` package
and this repository's `amora` code installed, download the `.agcm` from
your private Drive. Supply the same passphrase via a secret environment
variable, and run:

    python -m amora.storage.pcs4c_drive_backup recover \
      --archive /path/to/amora-pcs4c-YYYYMMDDTHHMMSSZ.agcm \
      --output-dir /path/to/new-empty-restored-directory \
      --confirm RECOVER_ENCRYPTED_PCS4C_OFFLINE

The authenticated decryption completes BEFORE either member is extracted,
then verifies the SQLite database and matching archive. Do not recover into
`/private`; use a new temporary private location.

The backup image is not intended as the long-term archive format itself.
The CLI must be retained in the public/private source repository to allow
restoration even if the Northflank Job is lost.

## After successful verified backup

Restore the prior original recovery Job settings/image when appropriate.
A Drive backup does not prove source freshness, actual free disk headroom,
production rollback readiness or that Aiven has imported data. The Aiven
`prepare` and private import commands remain separately gated.

Official references:
- https://developers.google.com/identity/protocols/oauth2/limited-input-device
- https://developers.google.com/workspace/drive/api/guides/manage-uploads
- https://developers.google.com/workspace/drive/api/guides/folder
- https://northflank.com/docs/v1/application/databases-and-persistence/add-a-volume

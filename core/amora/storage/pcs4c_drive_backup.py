"""Operator-only encrypted PCS4C backup to personal Drive using browser device consent.

No rclone account token, backup content, password, file hash, raw HTTP body or
Google OAuth secrets are printed/stored on the durable /private volume.
The existing PCS4C recovery Job retains exclusive ownership of its mounted
backup; the Google Drive file is ciphertext only.

The default CLI command is offline inspect. To transfer, the operator must
explicitly select 'backup' with a confirmation and configure strong encryption
passphrase + Google OAuth TV/limited-input client ID in runtime secrets.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import tarfile
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .aiven_private_import_operator import PrivateBridgeError, _audit_source
from .pcs4_recovery import _sha, verify_snapshot

_MAGIC = b"AMORA_PCS4C_AES256_GCM_V1\x00"
_SALT_SIZE = 16
_NONCE_SIZE = 12
_TAG_SIZE = 16
_KDF_N = 1 << 15
_KDF_R = 8
_KDF_P = 1
_CHUNK = 8 * 1024 * 1024
_COPY_CHUNK = 1024 * 1024
_MAX_DOWNLOAD = 160 * 1024 * 1024
_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
_GOOGLE_DEVICE_URL = "https://oauth2.googleapis.com/device/code"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_DRIVE_API = "https://www.googleapis.com/drive/v3/files"
_DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
_CONFIRM = "BACKUP_ENCRYPTED_PCS4C_TO_MY_GOOGLE_DRIVE"
_BASENAME = "amora-pre-pcs4c.db"
_NAMES = (_BASENAME, _BASENAME + ".avatar.zip")
_GOOGLE_ID = re.compile(r"^[0-9A-Za-z._-]{5,256}$")
_OAUTH_ID = re.compile(r"^[0-9A-Za-z-]+\.apps\.googleusercontent\.com$")


class CloudBackupError(RuntimeError):
    """Fixed sanitized failure code only."""


def _cryptography():
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Cipher, algorithms, modes, Scrypt


def _derive(passphrase: str, salt: bytes) -> bytes:
    _, _, _, Scrypt = _cryptography()
    if len(passphrase) < 20 or len(passphrase) > 1024:
        raise CloudBackupError("PCS4C_DRIVE_STRONG_PASSPHRASE_REQUIRED")
    return Scrypt(
        salt=salt, length=32, n=_KDF_N, r=_KDF_R, p=_KDF_P,
    ).derive(passphrase.encode("utf-8"))


class _GCMWriter:
    """Write-only encrypted stream, always authenticated at the end."""

    def __init__(self, destination, cryptor):
        self.destination = destination
        self.cryptor = cryptor

    def write(self, data: bytes) -> int:
        if data:
            self.destination.write(self.cryptor.update(data))
        return len(data)

    def flush(self):
        self.destination.flush()

    def finish(self):
        self.destination.write(self.cryptor.finalize())
        self.destination.write(self.cryptor.tag)
        self.destination.flush()


def encrypt_pair(
    snapshot: Path, sidecar: Path, encrypted: Path, passphrase: str,
) -> dict[str, str]:
    """Stream two fixed source members directly to AES-GCM; no temp clear tar."""
    Cipher, algorithms, modes, _ = _cryptography()
    if any(path.is_symlink() or not path.is_file() for path in (snapshot, sidecar)):
        raise CloudBackupError("PCS4C_DRIVE_SOURCE_PAIR_INVALID")
    salt, nonce = os.urandom(_SALT_SIZE), os.urandom(_NONCE_SIZE)
    cipher = Cipher(algorithms.AES(_derive(passphrase, salt)), modes.GCM(nonce))
    digests = {_NAMES[0]: _sha(snapshot), _NAMES[1]: _sha(sidecar)}
    try:
        with encrypted.open("xb") as target:
            target.write(_MAGIC + salt + nonce)
            cryptor = cipher.encryptor()
            cryptor.authenticate_additional_data(_MAGIC)
            sink = _GCMWriter(target, cryptor)
            with tarfile.open(fileobj=sink, mode="w|gz") as archive:
                for name, source in zip(_NAMES, (snapshot, sidecar)):
                    member = tarfile.TarInfo(name)
                    member.size = source.stat().st_size
                    member.mode = 0o400
                    member.mtime = 0
                    with source.open("rb") as stream:
                        archive.addfile(member, stream)
            sink.finish()
        if digests != {_NAMES[0]: _sha(snapshot), _NAMES[1]: _sha(sidecar)}:
            raise CloudBackupError("PCS4C_DRIVE_SOURCE_CHANGED")
        return digests  # internal only; NEVER print these
    except Exception:
        encrypted.unlink(missing_ok=True)
        raise


def restore_pair(
    encrypted: Path, restored_dir: Path, passphrase: str,
    *, verify_sqlite: bool = True,
) -> dict[str, Path]:
    """Authenticate GCM *before* tar parsing; reject any extra/malformed members."""
    Cipher, algorithms, modes, _ = _cryptography()
    restored_dir.mkdir(parents=True, exist_ok=False)
    clear_tar = restored_dir / "_authenticated_payload.tar.gz"
    try:
        with encrypted.open("rb") as encrypted_stream:
            header = encrypted_stream.read(len(_MAGIC) + _SALT_SIZE + _NONCE_SIZE)
            if len(header) != len(_MAGIC) + _SALT_SIZE + _NONCE_SIZE or not header.startswith(_MAGIC):
                raise CloudBackupError("PCS4C_DRIVE_ENCRYPTED_FORMAT_INVALID")
            salt = header[len(_MAGIC):len(_MAGIC) + _SALT_SIZE]
            nonce = header[-_NONCE_SIZE:]
            total = encrypted.stat().st_size
            payload_start = len(header)
            if total < payload_start + _TAG_SIZE:
                raise CloudBackupError("PCS4C_DRIVE_ENCRYPTED_FORMAT_INVALID")
            encrypted_stream.seek(total - _TAG_SIZE)
            tag = encrypted_stream.read(_TAG_SIZE)
            encrypted_stream.seek(payload_start)
            cipher = Cipher(algorithms.AES(_derive(passphrase, salt)), modes.GCM(nonce, tag))
            cryptor = cipher.decryptor()
            cryptor.authenticate_additional_data(_MAGIC)
            remaining = total - payload_start - _TAG_SIZE
            with clear_tar.open("xb") as destination:
                while remaining:
                    chunk = encrypted_stream.read(min(remaining, _COPY_CHUNK))
                    if not chunk:
                        raise CloudBackupError("PCS4C_DRIVE_CIPHERTEXT_TRUNCATED")
                    destination.write(cryptor.update(chunk))
                    remaining -= len(chunk)
                try:
                    destination.write(cryptor.finalize())
                except Exception:
                    raise CloudBackupError("PCS4C_DRIVE_AUTHENTICATION_FAILED") from None
        with tarfile.open(clear_tar, "r:gz") as archive:
            members = archive.getmembers()
            if (len(members) != 2 or {m.name for m in members} != set(_NAMES)
                    or any(not m.isfile() for m in members)):
                raise CloudBackupError("PCS4C_DRIVE_ARCHIVE_MEMBERS_INVALID")
            paths = {}
            for member in members:
                if member.size > _MAX_DOWNLOAD:
                    raise CloudBackupError("PCS4C_DRIVE_ARCHIVE_MEMBER_TOO_LARGE")
                target = restored_dir / member.name
                stream = archive.extractfile(member)
                if stream is None:
                    raise CloudBackupError("PCS4C_DRIVE_ARCHIVE_MEMBERS_INVALID")
                with stream, target.open("xb") as output:
                    copied = shutil.copyfileobj(stream, output, _COPY_CHUNK)
                if target.stat().st_size != member.size:
                    raise CloudBackupError("PCS4C_DRIVE_ARCHIVE_MEMBER_SIZE_MISMATCH")
                paths[member.name] = target
        clear_tar.unlink(missing_ok=True)
        if verify_sqlite:
            result = verify_snapshot(paths[_BASENAME], expected_schema=18)
            if result["database_sha256"] != _sha(paths[_BASENAME]):
                raise CloudBackupError("PCS4C_DRIVE_RESTORED_SQLITE_DRIFT")
        return paths
    except CloudBackupError:
        raise
    except Exception:
        raise CloudBackupError("PCS4C_DRIVE_RESTORE_FAILED") from None
    finally:
        clear_tar.unlink(missing_ok=True)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _http(method: str, url: str, *, body: bytes | None = None, headers=None,
          allow_host: str, stream_to: Path | None = None):
    """Strict-HTTPS, no redirected bearer credentials, bounded response."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != allow_host or parsed.username or parsed.password or parsed.port:
        raise CloudBackupError("PCS4C_DRIVE_NETWORK_DESTINATION_REJECTED")
    request = Request(url, data=body, method=method, headers=headers or {})
    opener = build_opener(_NoRedirect)
    try:
        try:
            response = opener.open(request, timeout=35)
        except HTTPError as err:
            # 308 Resume Incomplete carries confirmed Google server offsets.
            if err.code != 308:
                raise CloudBackupError("PCS4C_DRIVE_HTTP_REQUEST_FAILED") from None
            response = err
        with response:
            status = response.status if hasattr(response, "status") else response.code
            if stream_to is not None:
                if status != 200:
                    raise CloudBackupError("PCS4C_DRIVE_DOWNLOAD_REJECTED")
                total = 0
                with stream_to.open("xb") as target:
                    while True:
                        buf = response.read(_COPY_CHUNK)
                        if not buf:
                            break
                        total += len(buf)
                        if total > _MAX_DOWNLOAD:
                            raise CloudBackupError("PCS4C_DRIVE_DOWNLOAD_TOO_LARGE")
                        target.write(buf)
                return status, response.headers, b""
            result = response.read(128 * 1024 + 1)
            if len(result) > 128 * 1024:
                raise CloudBackupError("PCS4C_DRIVE_HTTP_RESPONSE_TOO_LARGE")
            return status, response.headers, result
    except CloudBackupError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        raise CloudBackupError("PCS4C_DRIVE_HTTP_REQUEST_FAILED") from None


def _json(data: bytes) -> dict:
    try:
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError("not object")
        return result
    except (ValueError, UnicodeDecodeError):
        raise CloudBackupError("PCS4C_DRIVE_JSON_INVALID") from None


def _google_authenticate(client_id: str, *, http=_http, pause=time.sleep,
                         monotonic=time.monotonic, notify=print) -> str:
    if not _OAUTH_ID.fullmatch(client_id):
        raise CloudBackupError("PCS4C_DRIVE_CLIENT_ID_INVALID")
    body = urlencode({"client_id": client_id, "scope": _DRIVE_SCOPE}).encode()
    status, _, data = http(
        "POST", _GOOGLE_DEVICE_URL, body=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        allow_host="oauth2.googleapis.com",
    )
    if status != 200:
        raise CloudBackupError("PCS4C_DRIVE_DEVICE_START_FAILED")
    offered = _json(data)
    verification = offered.get("verification_url")
    if verification not in {"https://www.google.com/device", "https://google.com/device"}:
        raise CloudBackupError("PCS4C_DRIVE_DEVICE_URL_INVALID")
    device_code, user_code = offered.get("device_code"), offered.get("user_code")
    if (not isinstance(device_code, str) or len(device_code) > 1000
            or not isinstance(user_code, str) or not re.fullmatch(r"[A-Z0-9-]{4,32}", user_code)):
        raise CloudBackupError("PCS4C_DRIVE_DEVICE_CODE_INVALID")
    expires = offered.get("expires_in")
    interval = offered.get("interval", 5)
    if not isinstance(expires, int) or expires < 30 or not isinstance(interval, int):
        raise CloudBackupError("PCS4C_DRIVE_DEVICE_RESPONSE_INVALID")
    interval = max(5, min(interval, 30))
    until = monotonic() + min(expires, 1200)
    # Only the USER code, never device_code, tokens or client secret.
    notify("PCS4C_DRIVE_AUTHORIZE " + json.dumps({
        "status": "waiting_for_google_consent",
        "verification_url": verification, "user_code": user_code,
        "expires_in_seconds": min(expires, 1200),
        "scope": "drive.file",
    }, sort_keys=True), flush=True)
    while monotonic() + interval < until:
        pause(interval)
        status, _, data = http(
            "POST", _GOOGLE_TOKEN_URL,
            body=urlencode({
                "client_id": client_id, "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            allow_host="oauth2.googleapis.com",
        )
        answer = _json(data)
        if status == 200 and answer.get("token_type") == "Bearer":
            if _DRIVE_SCOPE not in answer.get("scope", "").split():
                raise CloudBackupError("PCS4C_DRIVE_SCOPE_MISSING")
            token = answer.get("access_token")
            if not isinstance(token, str) or len(token) < 15:
                raise CloudBackupError("PCS4C_DRIVE_TOKEN_INVALID")
            return token
        if answer.get("error") == "authorization_pending":
            continue
        if answer.get("error") == "slow_down":
            interval = min(interval + 5, 35)
            continue
        raise CloudBackupError("PCS4C_DRIVE_CONSENT_DENIED_OR_FAILED")
    raise CloudBackupError("PCS4C_DRIVE_CONSENT_EXPIRED")


def _drive_json(method: str, url: str, token: str, metadata: dict | None = None,
                *, http=_http):
    headers = {"Authorization": "Bearer " + token}
    body = None
    if metadata is not None:
        body = json.dumps(metadata, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json; charset=UTF-8"
    status, response_headers, data = http(
        method, url, body=body, headers=headers, allow_host="www.googleapis.com",
    )
    if status not in (200, 201):
        raise CloudBackupError("PCS4C_DRIVE_GOOGLE_API_REJECTED")
    return _json(data), response_headers


def _upload(encrypted: Path, token: str, *, http=_http) -> tuple[str, str]:
    # drive.file cannot read unrelated files. Create app-owned folder and file.
    folder, _ = _drive_json(
        "POST", _DRIVE_API + "?fields=id,name",
        token,
        {"name": "Amora Backups Criptografados",
         "mimeType": "application/vnd.google-apps.folder"},
        http=http,
    )
    folder_id = folder.get("id")
    if not isinstance(folder_id, str) or not _GOOGLE_ID.fullmatch(folder_id):
        raise CloudBackupError("PCS4C_DRIVE_FOLDER_ID_INVALID")
    total = encrypted.stat().st_size
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "application/octet-stream",
        "X-Upload-Content-Length": str(total),
    }
    metadata = {
        "name": encrypted.name,
        "parents": [folder_id],
        "mimeType": "application/octet-stream",
    }
    status, reply_headers, _ = http(
        "POST", _DRIVE_UPLOAD + "?uploadType=resumable&fields=id,name,size,md5Checksum",
        body=json.dumps(metadata).encode(), headers=headers,
        allow_host="www.googleapis.com",
    )
    if status != 200:
        raise CloudBackupError("PCS4C_DRIVE_UPLOAD_START_FAILED")
    session = reply_headers.get("Location", "")
    parsed = urlsplit(session)
    if (parsed.scheme != "https" or parsed.hostname != "www.googleapis.com"
            or parsed.port or not parsed.path.startswith("/upload/drive/v3/files")
            or parsed.username or parsed.password):
        raise CloudBackupError("PCS4C_DRIVE_UPLOAD_SESSION_INVALID")
    file_id = None
    offset = 0
    with encrypted.open("rb") as source:
        while offset < total:
            chunk = source.read(min(_CHUNK, total - offset))
            if not chunk:
                raise CloudBackupError("PCS4C_DRIVE_SOURCE_STAGING_TRUNCATED")
            end = offset + len(chunk) - 1
            status, response_headers, data = http(
                "PUT", session, body=chunk,
                headers={
                    "Authorization": "Bearer " + token,
                    "Content-Type": "application/octet-stream",
                    "Content-Range": f"bytes {offset}-{end}/{total}",
                },
                allow_host="www.googleapis.com",
            )
            if end == total - 1:
                if status not in (200, 201):
                    raise CloudBackupError("PCS4C_DRIVE_UPLOAD_FINAL_UNKNOWN")
                created = _json(data)
                file_id = created.get("id")
            else:
                if status != 308 or response_headers.get("Range") != f"bytes=0-{end}":
                    raise CloudBackupError("PCS4C_DRIVE_UPLOAD_OFFSET_UNKNOWN")
            offset = end + 1
    if not isinstance(file_id, str) or not _GOOGLE_ID.fullmatch(file_id):
        raise CloudBackupError("PCS4C_DRIVE_UPLOAD_CONFIRMATION_INVALID")
    return folder_id, file_id


def _verify_remote(encrypted: Path, downloaded: Path, token: str, file_id: str,
                   original_hashes: dict[str, str], passphrase: str,
                   *, http=_http) -> None:
    details, _ = _drive_json(
        "GET", f"{_DRIVE_API}/{file_id}?fields=id,name,size,md5Checksum",
        token, http=http,
    )
    if (details.get("id") != file_id or details.get("name") != encrypted.name
            or str(details.get("size")) != str(encrypted.stat().st_size)):
        raise CloudBackupError("PCS4C_DRIVE_REMOTE_METADATA_MISMATCH")
    with encrypted.open("rb") as source:
        local_md5 = hashlib.file_digest(source, "md5").hexdigest()
    if details.get("md5Checksum") != local_md5:
        raise CloudBackupError("PCS4C_DRIVE_REMOTE_HASH_MISMATCH")
    status, _, _ = http(
        "GET", f"{_DRIVE_API}/{file_id}?alt=media",
        headers={"Authorization": "Bearer " + token},
        allow_host="www.googleapis.com", stream_to=downloaded,
    )
    if status != 200:
        raise CloudBackupError("PCS4C_DRIVE_DOWNLOAD_REJECTED")
    if _sha(downloaded) != _sha(encrypted):
        raise CloudBackupError("PCS4C_DRIVE_DOWNLOADED_CIPHERTEXT_MISMATCH")
    restored = downloaded.parent / "restored"
    outputs = restore_pair(downloaded, restored, passphrase)
    for name, expected in original_hashes.items():
        if _sha(outputs[name]) != expected:
            raise CloudBackupError("PCS4C_DRIVE_RESTORED_HASH_MISMATCH")


def _source_inspect() -> dict:
    try:
        return _audit_source()
    except PrivateBridgeError:
        raise CloudBackupError("PCS4C_DRIVE_SOURCE_VERIFICATION_BLOCKED") from None


def backup_to_drive(passphrase: str, client_id: str, *, http=_http,
                    authorize=_google_authenticate, upload=_upload,
                    verify_remote=_verify_remote) -> dict:
    source_report = _source_inspect()
    from .aiven_snapshot_audit import DEFAULT_SNAPSHOT
    snapshot = DEFAULT_SNAPSHOT
    sidecar = Path(str(snapshot) + ".avatar.zip")
    if len(passphrase) < 20:
        raise CloudBackupError("PCS4C_DRIVE_STRONG_PASSPHRASE_REQUIRED")
    if not _OAUTH_ID.fullmatch(client_id):
        raise CloudBackupError("PCS4C_DRIVE_CLIENT_ID_INVALID")
    with tempfile.TemporaryDirectory(prefix="amora-encrypted-cloud-backup-") as tmp:
        folder = Path(tmp)
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        encrypted = folder / f"amora-pcs4c-{timestamp}.agcm"
        hashes = encrypt_pair(snapshot, sidecar, encrypted, passphrase)
        if source_report["snapshot_bytes"] != snapshot.stat().st_size:
            raise CloudBackupError("PCS4C_DRIVE_SOURCE_CHANGED")
        # Consent happens once per manual job run. Refresh tokens stay in memory
        # and are discarded on exit; no OAuth state is saved under /private.
        token = authorize(client_id, http=http)
        drive_folder_id, file_id = upload(encrypted, token, http=http)
        verify_remote(
            encrypted, folder / "downloaded.agcm", token, file_id, hashes,
            passphrase, http=http,
        )
        return {
            "status": "encrypted_backup_uploaded_and_restored",
            "provider": "google-drive",
            "encryption": "AES-256-GCM+Scrypt",
            "private_rows": source_report["logical_rows"],
            "schema_version": 18,
            "encrypted_bytes": encrypted.stat().st_size,
            "drive_folder_url": "https://drive.google.com/drive/folders/" + drive_folder_id,
            "remote_ciphertext_download_verified": True,
            "sqlite_restore_from_drive_verified": True,
            "backup_contents_logged": False,
            "google_tokens_persisted": False,
            "private_source_modified": False,
            "postgres_import_performed": False,
            "production_cutover_authorized": False,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inspect", "backup", "recover"))
    parser.add_argument("--confirm", default="")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.mode == "inspect":
            source = _source_inspect()
            result = {
                "status": "pcs4c_backup_ready_for_encryption",
                "operation": "offline_read_only_inspect",
                "schema_version": 18,
                "logical_rows": source["logical_rows"],
                "private_snapshot_bytes": source["snapshot_bytes"],
                "network_access": False,
                "cloud_backup_performed": False,
                "private_source_modified": False,
            }
        elif args.mode == "backup":
            if args.confirm != _CONFIRM:
                raise CloudBackupError("PCS4C_DRIVE_OPERATOR_CONFIRMATION_REQUIRED")
            result = backup_to_drive(
                os.environ.get("AMORA_BACKUP_PASSPHRASE", ""),
                os.environ.get("AMORA_GOOGLE_DRIVE_OAUTH_CLIENT_ID", ""),
            )
        else:
            if args.confirm != "RECOVER_ENCRYPTED_PCS4C_OFFLINE" or not args.archive or not args.output_dir:
                raise CloudBackupError("PCS4C_DRIVE_RECOVERY_CONFIRMATION_REQUIRED")
            outputs = restore_pair(
                args.archive, args.output_dir,
                os.environ.get("AMORA_BACKUP_PASSPHRASE", ""),
            )
            result = {
                "status": "encrypted_archive_recovered_offline",
                "files_restored": len(outputs),
                "schema_version": 18,
                "network_access": False,
                "production_cutover_authorized": False,
            }
    except (CloudBackupError, PrivateBridgeError) as exc:
        reason = str(exc)
        if not (reason.startswith("PCS4C_DRIVE_")
                and re.fullmatch(r"PCS4C_DRIVE_[A-Z0-9_]+", reason)):
            reason = "PCS4C_DRIVE_OPERATION_BLOCKED"
        result = {"status": "blocked", "reason": reason,
                  "postgres_import_performed": False,
                  "production_cutover_authorized": False}
    except Exception:
        result = {"status": "blocked", "reason": "PCS4C_DRIVE_UNEXPECTED_FAILURE",
                  "postgres_import_performed": False,
                  "production_cutover_authorized": False}
    print("PCS4C_DRIVE_BACKUP " + json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["status"] in {
        "pcs4c_backup_ready_for_encryption",
        "encrypted_backup_uploaded_and_restored",
        "encrypted_archive_recovered_offline",
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())

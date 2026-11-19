"""One-shot restore rehearsal of encrypted PCS4C artifact into disposable PG18.

Operator's encrypted artifact is downloaded into a private ephemeral runner;
source files are never uploaded, published, echoed or committed. This is not
a real Aiven cutover or proof of the user's offsite Drive copy.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

class RehearsalFailure(Exception):
    pass

def _extract(archive: Path, folder: Path) -> Path:
    import pyzipper
    filename = "amora-pre-pcs4c.db"
    sidecar = filename + ".avatar.zip"
    allowed = {filename, sidecar}
    if not archive.is_file():
        raise RehearsalFailure("ARTIFACT_MISSING")
    secret = os.environ.get("AMORA_BACKUP_PASSPHRASE", "")
    if len(secret) < 32:
        raise RehearsalFailure("PASSPHRASE_UNAVAILABLE")
    with pyzipper.AESZipFile(archive, "r") as src:
        if set(src.namelist()) != allowed:
            raise RehearsalFailure("ARCHIVE_CONTENTS_INVALID")
        for name in (filename, sidecar):
            with src.open(name, pwd=secret.encode("utf-8")) as stream:
                destination = folder / name
                fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "wb") as dest:
                    shutil.copyfileobj(stream, dest, length=1024 * 1024)
    return folder / filename

def _fingerprint(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()

def _run(archive: Path, folder: Path) -> dict[str, object]:
    import psycopg
    from amora.storage.postgres_run3_snapshot_preflight import inspect_private_snapshot, Run3SnapshotError
    from amora.storage.pcs4_recovery import verify_snapshot
    from amora.storage.postgres_staging_schema_prepare import prepare_empty_staging_schema
    from amora.storage.postgres_private_staging_import import import_private_staging_v18
    from amora.storage.postgres_run3_preflight import inspect_prepared_postgres
    from amora.storage.postgres_runtime_connection import PostgresTargetPolicy, PostgresBackendCandidate
    from amora.storage.postgres_runtime_registry import compiled_domain_registry

    print("AMORA_RESTORE_STAGE archive_decrypt", flush=True)
    snapshot = _extract(archive, folder)
    sidecar = Path(str(snapshot) + ".avatar.zip")
    before = (_fingerprint(snapshot), _fingerprint(sidecar))
    print("AMORA_RESTORE_STAGE pcs4c_verify", flush=True)
    try:
        verify_snapshot(snapshot, expected_schema=18)
    except RehearsalFailure:
        raise
    except Exception:
        raise RehearsalFailure("PCS4C_VERIFICATION_BLOCKED") from None
    print("AMORA_RESTORE_STAGE source_canonical_ddl", flush=True)
    try:
        verified = inspect_private_snapshot(snapshot)
    except Run3SnapshotError as exc:
        code = str(exc)
        if re.fullmatch(r"RUN3_[A-Z_]{10,80}", code) is None:
            code = "RUN3_SOURCE_CERTIFICATION_BLOCKED"
        raise RehearsalFailure(code) from None
    if verified["status"] != "verified_offline":
        raise RehearsalFailure("SOURCE_AUDIT_BLOCKED")
    target = PostgresTargetPolicy(host="127.0.0.1", port=5432,
                                  database="amora_pg_synthetic",
                                  schema="amora_pg_v18_lab", server_major=18)
    def connect():
        return psycopg.connect(host="127.0.0.1", port=5432,
            dbname="amora_pg_synthetic", user="amora_ci",
            password="synthetic_only", autocommit=True, connect_timeout=8)

    with closing(connect()) as raw:
        print("AMORA_RESTORE_STAGE pg18_prepare", flush=True)
        prepared = prepare_empty_staging_schema(raw, target,
            confirmation="PREPARE_EMPTY_PG18_ISOLATED_STAGING_ONLY")
        if prepared["status"] != "empty_staging_schema_prepared":
            raise RehearsalFailure("PG_SCHEMA_PREPARATION_BLOCKED")
        print("AMORA_RESTORE_STAGE pg18_import", flush=True)
        imported = import_private_staging_v18(snapshot, raw, target,
            confirmation="IMPORT_VERIFIED_V18_TO_EMPTY_ISOLATED_STAGING")
        if imported["status"] != "staged_import_completed" or imported["typed_row_parity"] is not True:
            raise RehearsalFailure("ROW_PARITY_BLOCKED")
        print("AMORA_RESTORE_STAGE pg18_structure", flush=True)
        structural = inspect_prepared_postgres(raw, target, require_memory_epoch=True)
        if structural.status != "structural_pass":
            raise RehearsalFailure("POST_IMPORT_STRUCTURE_BLOCKED")
        disk = int(raw.execute("SELECT pg_database_size(current_database())").fetchone()[0])
    if before != (_fingerprint(snapshot), _fingerprint(sidecar)):
        raise RehearsalFailure("SOURCE_CHANGED")
    # On a disposable clone, run the existing Core bootstrap with no remote
    # providers, Discord connection or production credentials.
    print("AMORA_RESTORE_STAGE core_import", flush=True)
    from amora.app import AmoraCore
    from amora.time import SimulatedClock
    os.environ["AMORA_MODE"] = "local"
    os.environ["AMORA_HOME"] = str(folder / "core-home")
    os.environ.pop("AMORA_SECRET_LOCAL_API_TOKEN", None)
    backend = PostgresBackendCandidate(
        connect, compiled_domain_registry(), target=target, max_connections=16,
    )
    print("AMORA_RESTORE_STAGE core_bootstrap", flush=True)
    boot = "blocked"
    bootstrap_error = "none"
    bootstrap_origin = "none"
    try:
        core = AmoraCore.bootstrap(
            clock=SimulatedClock(datetime(2026, 11, 19, tzinfo=timezone.utc)),
            storage=backend,
        )
        try:
            boot = "bootstrapped"
        finally:
            core.close()
    except Exception as exc:
        boot = "blocked"
        candidate = str(exc)
        bootstrap_error = (
            candidate if re.fullmatch(r"PG_[A-Z0-9_]{6,90}", candidate)
            else type(exc).__name__
            if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,60}", type(exc).__name__)
            else "UNCLASSIFIED"
        )
        frame = exc.__traceback__
        while frame is not None and frame.tb_next is not None:
            frame = frame.tb_next
        if frame is not None:
            origin = frame.tb_frame.f_code.co_name
            bootstrap_origin = (
                origin if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,60}", origin)
                else "UNCLASSIFIED"
            )

    return {
        "status": "verified_ephemeral_pg18_restore",
        "snapshot_logical_tables": verified["logical_tables"],
        "snapshot_rows": verified["logical_rows"],
        "source_bytes": verified["snapshot_bytes"],
        "target_database_bytes": disk,
        "row_parity": True,
        "schema": 18,
        "structural_pass": True,
        "core_bootstrap": boot,
        "core_bootstrap_error": bootstrap_error,
        "core_bootstrap_origin": bootstrap_origin,
        "source_unchanged": True,
        "production_aiven_changed": False,
        "production_cutover_authorized": False,
        "offsite_user_copy_verified": False,
        "capacity_on_aiven_free_verified": False,
    }

def main() -> int:
    stage = "ephemeral_restore"
    try:
        archive = Path(os.environ["RUNNER_TEMP"]) / "amora-rehearsal-artifact" / "amora-backup.zip"
        with tempfile.TemporaryDirectory(prefix="amora-restore-rehearsal-") as temp:
            os.chmod(temp, 0o700)
            result = _run(archive, Path(temp))
        print("AMORA_RESTORE_REHEARSAL " + json.dumps(result, sort_keys=True))
        return 0 if result["core_bootstrap"] == "bootstrapped" else 2
    except RehearsalFailure as exc:
        reason = str(exc)
        if re.fullmatch(r"[A-Z0-9_]{8,80}", reason) is None:
            reason = "REHEARSAL_FAILED"
    except Exception:
        reason = "REHEARSAL_FAILED"
    print("AMORA_RESTORE_REHEARSAL " + json.dumps({
        "status": "blocked", "reason": reason,
        "real_aiven_changed": False, "production_cutover_authorized": False,
    }, sort_keys=True))
    return 2

if __name__ == "__main__":
    raise SystemExit(main())

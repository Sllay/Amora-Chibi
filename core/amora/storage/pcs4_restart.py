"""PCS.4C final gate: independent Core boots and canonical memory ranking.

Operator-only, after verified restore. Never start HTTP, Discord or scheduler.
A child process receives only isolated destination credentials, never source
credentials or provider keys. Core bootstrap may write to the isolated target.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from .backend import backend_from_environment
from .schema import LATEST_SCHEMA_VERSION
from .pcs4_recovery import (
    compare_snapshots, destination_from_environment, verify_snapshot, _open, _row_hashes, _sha,
)


def _normalize(value):
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((_normalize(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True, default=str))
    if isinstance(value, (tuple, list)):
        return [_normalize(v) for v in value]
    return value


def _fingerprint(hits):
    payload = [_normalize(hit.model_dump(mode="python")) for hit in hits]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str,
                                     ensure_ascii=False).encode()).hexdigest()


def _boot(backend, timestamp):
    """One actual Core initialization and close; no provider inference."""
    from amora.app import AmoraCore
    from amora.events import PrivacyScope
    from amora.memory import MemoryQuery
    from amora.time import SimulatedClock

    core = AmoraCore.bootstrap(storage=backend, clock=SimulatedClock(timestamp))
    try:
        queries = (
            MemoryQuery(projection_recipient_id="PERSON_BK"),
            MemoryQuery(projection_recipient_id="PERSON_BK", projection_remote_only=True),
            MemoryQuery(projection_public_only=True, projection_remote_only=True),
            MemoryQuery(projection_recipient_id="PERSON_BK", include_archived=True),
            MemoryQuery(text="projeto memória conversa", projection_recipient_id="PERSON_BK",
                        projection_remote_only=True),
        )
        results = [core.memory.store.rank(query) for query in queries]
        for index, hits in enumerate(results):
            for hit in hits:
                record = hit.memory
                if record.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
                    raise ValueError("PCS4C_RECALL_PRIVACY")
                if index != 3 and record.archived_at is not None:
                    raise ValueError("PCS4C_RECALL_ARCHIVED")
                if index in (1, 2, 4) and not record.remote_processing_allowed:
                    raise ValueError("PCS4C_RECALL_REMOTE")
                if index == 2 and record.privacy_scope != PrivacyScope.PUBLIC:
                    raise ValueError("PCS4C_RECALL_PUBLIC")
                if record.privacy_scope != PrivacyScope.PUBLIC:
                    allowed = set(record.audience)
                    if record.owner_entity_id:
                        allowed.add(record.owner_entity_id)
                    if not allowed:
                        allowed.add(record.provenance.actor_id)
                    if "PERSON_BK" not in allowed:
                        raise ValueError("PCS4C_RECALL_OWNER")
        return {"fingerprints": [_fingerprint(hits) for hits in results],
                "hit_counts": [len(hits) for hits in results],
                "private_hits": sum(h.memory.privacy_scope == PrivacyScope.PRIVATE for h in results[0]),
                "archived_hits": sum(h.memory.archived_at is not None for h in results[3]),
                "public_hits": len(results[2]),
                "nonremote_hits": sum(not h.memory.remote_processing_allowed for h in results[0])}
    finally:
        core.close()


def _child_env(home, backend=None):
    # No inherited AMORA_*, model keys, identity binding files or source token.
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "SSL_CERT_FILE",
                                      "SSL_CERT_DIR") if k in os.environ}
    env.update(HOME=str(home), AMORA_HOME=str(home), AMORA_MODE="local",
               AMORA_ASSET_STORAGE="database", AMORA_STORAGE_BACKEND="sqlite",
               PYTHONDONTWRITEBYTECODE="1",
               # The validation image uses a source checkout and a test-only
               # interpreter. Resolve our own package root, never inherit an
               # arbitrary PYTHONPATH from the operator environment.
               PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    if backend is not None:
        env.update(AMORA_STORAGE_BACKEND="libsql", AMORA_LIBSQL_URL=backend.url,
                   AMORA_SECRET_LIBSQL_AUTH_TOKEN=backend._token)
    return env


def _run_boot(home, timestamp, backend=None):
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    result = home / "pcs4c-result.json"
    # Child logs never enter the operator report; private temporary home is
    # removed by the parent. Separate processes model real restart.
    subprocess.run([sys.executable, "-m", __name__ if __name__ != "__main__"
                    else "amora.storage.pcs4_restart", "_boot",
                    timestamp.isoformat(), str(result)],
                   env=_child_env(home, backend), stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=True, timeout=180)
    return json.loads(result.read_text())


def certify_restart(snapshot, backend, *, expected_schema=LATEST_SCHEMA_VERSION):
    snapshot = Path(snapshot)
    verified = verify_snapshot(snapshot, expected_schema=expected_schema)
    with tempfile.TemporaryDirectory(prefix="pcs4c-restart-") as directory:
        root = Path(directory)
        # Confirm destination still matches the exact verified backup before
        # any Core boot. A nonmatching target is never bootstrapped.
        recovered = root / "before.db"
        with closing(backend.connect(recovered)) as connection:
            backend.backup(connection, recovered)
        equal_tables = compare_snapshots(snapshot, recovered)
        if equal_tables != verified["tables"]:
            raise ValueError("PCS4C_RESTART_TABLES")
        timestamp = datetime.now(timezone.utc)
        baseline = root / "baseline"
        (baseline / "data").mkdir(parents=True, mode=0o700)
        shutil.copyfile(snapshot, baseline / "data" / "amora.db")
        expected = _run_boot(baseline, timestamp)
        first = _run_boot(root / "first", timestamp, backend)
        second = _run_boot(root / "second", timestamp, backend)
        if expected != first or first != second:
            raise ValueError("PCS4C_RESTART_RANK_MISMATCH")
        if not any(expected["hit_counts"]):
            raise ValueError("PCS4C_RESTART_EMPTY_RECALL")
        after = root / "after.db"
        with closing(backend.connect(after)) as connection:
            backend.backup(connection, after)
        memory_tables = ("memory_records", "memory_entities", "memory_tags", "memory_fts")
        with closing(_open(snapshot)) as original, closing(_open(after)) as restarted:
            if any(_row_hashes(original, table) != _row_hashes(restarted, table)
                   for table in memory_tables):
                raise ValueError("PCS4C_RESTART_MEMORY_CHANGED")
        if expected_schema == 19:
            # No runtime worker is enabled during the drill. Job state and
            # evidence links must also survive both boots unchanged.
            with closing(_open(snapshot)) as original, closing(_open(after)) as restarted:
                if any(_row_hashes(original, table) != _row_hashes(restarted, table)
                       for table in ("sensory_episode_jobs", "sensory_episode_job_evidence",
                                     "sensory_episode_invalidations")):
                    raise ValueError("PCS4C_RESTART_EPISODE_WORK_CHANGED")
        if (_sha(snapshot) != verified["database_sha256"] or
                _sha(Path(str(snapshot) + ".avatar.zip")) != verified["archive_sha256"]):
            raise ValueError("PCS4C_SOURCE_CHANGED")
        return {"schema_version": verified["schema_version"], "equal_tables": equal_tables,
                "tables": verified["tables"], "boots": 2, "query_count": 5,
                "rank_equal": True, "memory_tables_equal": len(memory_tables),
                **({"episode_work_tables_equal": 3} if expected_schema == 19 else {}),
                **{k: v for k, v in expected.items() if k != "fingerprints"}}


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "_boot":
        # Private subprocess entry point; parent controls env and paths.
        logging.disable(logging.CRITICAL)
        result = _boot(backend_from_environment(), datetime.fromisoformat(args[1]))
        Path(args[2]).write_text(json.dumps(result))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--isolated-restored-destination", action="store_true", required=True)
    parsed = parser.parse_args(args)
    try:
        report = certify_restart(parsed.snapshot, destination_from_environment())
        report.update(status="ok", runtime_projection_enabled=False)
    except Exception as exc:
        report = {"status": "error", "exception_type": type(exc).__name__,
                  "runtime_projection_enabled": False}
        if type(exc) is ValueError and exc.args and exc.args[0] in {
            "PCS4C_RESTART_TABLES", "PCS4C_RESTART_RANK_MISMATCH",
            "PCS4C_RESTART_EMPTY_RECALL", "PCS4C_RESTART_MEMORY_CHANGED", "PCS4C_SOURCE_CHANGED",
            "PCS4C_DESTINATION_IS_SOURCE",
            "PCS4C_DESTINATION_TOKEN_REUSED", "PCS4C_MISSING_CONFIG",
        }:
            report["reason"] = exc.args[0]
    print("PCS4C_RESTART " + json.dumps(report, separators=(",", ":")))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

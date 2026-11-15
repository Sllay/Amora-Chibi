"""One-shot, read-only PCS.4A certification against the configured Turso.

Uses the existing HTTP transport and a disposable SQLite memory-domain copy.
No SDK dependency, schema writes, access counter writes or model calls.
This is a diagnostic, not an activated replica or a backup.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing

from amora.memory import MemoryQuery
from amora.memory.store import MemoryStore
from amora.settings import RuntimePaths
from amora.storage.backend import StorageCapabilities
from amora.storage.database import AmoraDatabase
from amora.storage.remote import LibSQLBackend
from amora.storage.schema import LATEST_SCHEMA_VERSION
from amora.time import SimulatedClock, SystemClock

logger = logging.getLogger(__name__)
TABLES = ("entities", "aliases", "relationships", "memory_records",
          "memory_entities", "memory_tags", "memory_fts")
MAX_ROWS = 10_000
MAX_BYTES = 4 * 1024 * 1024
FETCH_BATCH = 128
MIN_DISK = 64 * 1024 * 1024
MAX_SECONDS = 25


class ProbeLimit(RuntimeError):
    pass


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _read_number(path):
    try:
        value = Path(path).read_text().strip()
        return int(value) if value.isdigit() else None
    except OSError:
        return None


def resources(path):
    rss = None
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                rss = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError):
        pass
    current = _read_number("/sys/fs/cgroup/memory.current")
    limit = _read_number("/sys/fs/cgroup/memory.max")
    if current is None:
        current = _read_number("/sys/fs/cgroup/memory/memory.usage_in_bytes")
        limit = _read_number("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    # Linux cgroup v1 uses a very large sentinel for an unlimited group.
    if limit is not None and limit >= 2**60:
        limit = None
    return {"process_rss_bytes": rss,
            "cgroup_current_bytes": current,
            "cgroup_limit_bytes": limit,
            "disk_free_bytes": shutil.disk_usage(path).free}


class BorrowedBackend:
    capabilities = StorageCapabilities("pcs4-probe", False, False)

    def __init__(self, connection):
        self.connection = connection

    def connect(self, path):
        return self.connection


class ReadProbeStore(MemoryStore):
    def _ensure_fts(self):
        return True

    def search(self, query):
        raise sqlite3.OperationalError("PCS4_READ_ONLY")


class BoundedReader:
    def __init__(self, connection, deadline):
        self.connection, self.deadline = connection, deadline
        self.calls = 0

    def execute(self, sql, parameters=()):
        if time.monotonic() >= self.deadline:
            raise ProbeLimit("TIME_BUDGET")
        if not sql.strip().upper().startswith(("SELECT ", "SELECT\n", "PRAGMA TABLE_INFO(")):
            raise sqlite3.OperationalError("PCS4_READ_ONLY")
        self.calls += 1
        return self.connection.execute(sql, parameters)


def _copy_table(reader, target, table, definition, remaining_rows, remaining_bytes):
    quoted = _quote(table)
    columns = [row["name"] for row in reader.execute("PRAGMA table_info(" + quoted + ")").fetchall()]
    if not columns:
        raise ProbeLimit("MISSING_COLUMNS")
    # Measure the size before fetching content; numeric/NULL values are bounded
    # too. The estimate is logical cell bytes, not an exact RSS prediction.
    lengths = " + ".join("COALESCE(length(CAST(" + _quote(col) + " AS BLOB)),0)" for col in columns)
    row = reader.execute("SELECT COUNT(*),COALESCE(SUM(" + lengths + "),0) FROM " + quoted).fetchone()
    count, size = int(row[0]), int(row[1])
    if count > remaining_rows or size > remaining_bytes:
        raise ProbeLimit("SNAPSHOT_BUDGET")
    target.execute(definition)
    # Keyset pagination bounds transport decoding and preserves FTS row order.
    # No OFFSET or unbounded response allocation, even within the byte cap.
    copied, last_rowid = 0, None
    insert = "INSERT INTO " + quoted + " VALUES (" + ",".join("?" for _ in columns) + ")"
    while copied < count:
        where = "" if last_rowid is None else " WHERE rowid > ?"
        args = (FETCH_BATCH,) if last_rowid is None else (last_rowid, FETCH_BATCH)
        rows = reader.execute("SELECT rowid AS pcs4_rowid,* FROM " + quoted + where +
                              " ORDER BY rowid LIMIT ?", args).fetchall()
        if not rows:
            raise ProbeLimit("SNAPSHOT_COUNT_CHANGED")
        target.executemany(insert, [tuple(row[col] for col in columns) for row in rows])
        last_rowid = rows[-1]["pcs4_rowid"]
        copied += len(rows)
    if copied != count:
        raise ProbeLimit("SNAPSHOT_COUNT_CHANGED")
    return count, size


def certify(backend, cache_dir):
    """Return numeric/boolean results only; never return records or prompt text."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    before = resources(cache_dir)
    if before["disk_free_bytes"] < MIN_DISK:
        raise ProbeLimit("DISK_BUDGET")
    current, limit = before["cgroup_current_bytes"], before["cgroup_limit_bytes"]
    if current is not None and limit is not None and limit - current < MIN_DISK:
        raise ProbeLimit("RAM_HEADROOM")
    started = time.monotonic()
    deadline = started + MAX_SECONDS
    clock = SimulatedClock(SystemClock().now())
    queries = (
        MemoryQuery(text="", entity_ids={"PERSON_BK"}, projection_recipient_id="PERSON_BK",
                    projection_remote_only=True),
        MemoryQuery(text="projeto memória conversa", entity_ids={"PERSON_BK"},
                    projection_recipient_id="PERSON_BK", projection_remote_only=True),
    )
    with tempfile.TemporaryDirectory(prefix="pcs4-", dir=cache_dir) as directory:
        path = Path(directory) / "memory.db"
        with closing(backend.connect(path)) as primary, closing(sqlite3.connect(path)) as target:
            target.row_factory = sqlite3.Row
            target.execute("PRAGMA foreign_keys=OFF")
            target.execute("PRAGMA cache_size=-1024")
            reader = BoundedReader(primary, deadline)
            total_rows = total_bytes = 0
            with primary.read_transaction():
                version = reader.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
                if version != LATEST_SCHEMA_VERSION:
                    raise ProbeLimit("SCHEMA_NOT_CERTIFIED")
                schema = reader.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema "
                                        "WHERE sql IS NOT NULL ORDER BY rowid").fetchall()
                definitions = {row["name"]: row["sql"] for row in schema if row["type"] == "table"}
                if any(table not in definitions for table in TABLES):
                    raise ProbeLimit("SCHEMA_MISSING_TABLE")
                for table in TABLES:
                    count, size = _copy_table(reader, target, table, definitions[table],
                                              MAX_ROWS-total_rows, MAX_BYTES-total_bytes)
                    total_rows += count
                    total_bytes += size
                for row in schema:
                    if row["type"] == "index" and row["tbl_name"] in TABLES:
                        target.execute(row["sql"])
                target.commit()
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ProbeLimit("LOCAL_INTEGRITY")
                # Both rankings use the same persisted identities and fixed clock.
                identity_db = AmoraDatabase(path, backend=BorrowedBackend(reader))
                identities = identity_db.load_registry()
                if identities is None:
                    raise ProbeLimit("NO_IDENTITIES")
                remote = ReadProbeStore(path, clock, identities, backend=BorrowedBackend(reader))
                expected, remote_ms, remote_calls = [], [], []
                for query in queries:
                    tick, calls = time.monotonic(), reader.calls
                    expected.append(remote.rank(query))
                    remote_ms.append(round((time.monotonic()-tick)*1000, 3))
                    remote_calls.append(reader.calls-calls)
            # Local ranking occurs after releasing the remote snapshot.
            local = ReadProbeStore(path, clock, identities, backend=BorrowedBackend(target))
            actual, local_ms = [], []
            for query in queries:
                tick = time.monotonic()
                actual.append(local.rank(query))
                local_ms.append(round((time.monotonic()-tick)*1000, 3))
            equivalent = [a == b for a, b in zip(expected, actual)]
            after = resources(cache_dir)
            return {"status": "ok" if all(equivalent) else "mismatch",
                    "mode": "read_only_snapshot", "schema_version": version,
                    "table_count": len(TABLES), "rows": total_rows,
                    "logical_bytes": total_bytes, "snapshot_bytes": path.stat().st_size,
                    "remote_rank_ms": remote_ms, "local_rank_ms": local_ms,
                    "remote_rank_calls": remote_calls, "rank_equal": equivalent,
                    "hit_counts": [len(hits) for hits in expected],
                    "total_ms": round((time.monotonic()-started)*1000, 3),
                    "resources_before": before, "resources_after": after,
                    "runtime_projection_enabled": False}


def run():
    """Use existing injected config. Never print exception messages or secrets."""
    try:
        if os.getenv("AMORA_STORAGE_BACKEND") != "libsql":
            raise ProbeLimit("REQUIRES_LIBSQL")
        backend = LibSQLBackend(os.getenv("AMORA_LIBSQL_URL", ""),
                               os.getenv("AMORA_SECRET_LIBSQL_AUTH_TOKEN", ""), timeout=5.0)
        report = certify(backend, RuntimePaths.from_environment().cache)
    except ProbeLimit as exc:
        report = {"status": "skipped", "reason": str(exc), "runtime_projection_enabled": False}
    except Exception as exc:
        report = {"status": "error", "exception_type": type(exc).__name__,
                  "runtime_projection_enabled": False}
    logger.info("PCS4_PREFLIGHT %s", json.dumps(report, separators=(",", ":")))
    return report


if __name__ == "__main__":
    report = run()
    print("PCS4_PREFLIGHT " + json.dumps(report, separators=(",", ":")))
    raise SystemExit(0 if report["status"] == "ok" else 1)

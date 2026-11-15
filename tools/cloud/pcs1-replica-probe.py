#!/usr/bin/env python3
"""Native libSQL replica experiment against a disposable LOOPBACK sqld only.

Requires libsql==0.1.11 and a fresh sqld primary on 127.0.0.1:8080.
No Amora runtime, credentials, production endpoint or personal data.
This is a capability probe, not a production backend.
"""
from __future__ import annotations

import json
from pathlib import Path
import resource
import sqlite3
import tempfile
import time

import libsql

URL = "http://127.0.0.1:8080"


def connect(path):
    return libsql.connect(str(path), sync_url=URL, auth_token="")


def scalar(connection, sql, args=()):
    return connection.execute(sql, args).fetchone()[0]


def main():
    start = time.perf_counter()
    report = {}
    with tempfile.TemporaryDirectory(prefix="amora-pcs1-") as directory:
        root = Path(directory)
        first = connect(root / "first.db")
        second = None
        reopened = None
        try:
            first.sync()
            if scalar(first, "SELECT COUNT(*) FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                raise RuntimeError("PCS1_REQUIRES_EMPTY_DISPOSABLE_SERVER")
            first.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY, body TEXT NOT NULL, tags TEXT NOT NULL)")
            first.execute("CREATE VIRTUAL TABLE probe_fts USING fts5(body)")
            first.execute("INSERT INTO probe VALUES (1, 'fixture aurora', '[\"drawing\"]')")
            first.execute("INSERT INTO probe_fts VALUES ('fixture aurora')")
            first.commit()
            assert scalar(first, "SELECT count(*) FROM probe") == 1
            assert scalar(first, "SELECT count(*) FROM probe_fts WHERE probe_fts MATCH 'aurora'") == 1
            assert scalar(first, "SELECT json_extract(tags, '$[0]') FROM probe") == "drawing"
            report["read_own_write_fts_json"] = True

            # The current Core has multiple connections/stores. Separate replica
            # files first, then test same-file handles explicitly.
            second = connect(root / "second.db")
            second.sync()
            assert scalar(second, "SELECT count(*) FROM probe") == 1
            first.execute("INSERT INTO probe VALUES (2, 'second fixture', '[]')")
            first.commit()
            second.sync()
            assert scalar(second, "SELECT count(*) FROM probe") == 2
            report["cross_replica_sync"] = True

            try:
                first.execute("BEGIN IMMEDIATE")
                first.execute("INSERT INTO probe VALUES (3, 'rollback fixture', '[]')")
                first.rollback()
                second.sync()
                report["begin_immediate_rollback"] = scalar(second, "SELECT count(*) FROM probe") == 2
            except Exception as exc:
                report["begin_immediate_rollback"] = False
                report["begin_immediate_error_type"] = type(exc).__name__
                try:
                    first.rollback()
                except Exception:
                    pass

            try:
                first.execute("INSERT INTO probe VALUES (1, 'duplicate', '[]')")
                first.commit()
                report["unique_constraint"] = False
            except Exception as exc:
                report["unique_constraint"] = True
                report["unique_exception_type"] = type(exc).__name__
                report["sqlite_integrity_error"] = isinstance(exc, sqlite3.IntegrityError)
                try:
                    first.rollback()
                except Exception:
                    pass

            # Check driver surface, without assuming sqlite3 compatibility is exact.
            cursor = first.execute("SELECT * FROM probe ORDER BY id")
            report["cursor_rowcount_supported"] = hasattr(cursor, "rowcount")
            report["row_factory_supported"] = hasattr(first, "row_factory")
            report["in_transaction_supported"] = hasattr(first, "in_transaction")
            report["context_manager_supported"] = hasattr(first, "__enter__")
            report["executemany_supported"] = hasattr(first, "executemany")
            try:
                reopened = connect(root / "first.db")
                reopened.sync()
                report["same_file_multiple_handles"] = scalar(reopened, "SELECT count(*) FROM probe") == 2
            except Exception as exc:
                report["same_file_multiple_handles"] = False
                report["same_file_error_type"] = type(exc).__name__
            finally:
                if reopened is not None:
                    reopened.close()
                    reopened = None
        finally:
            if second is not None:
                second.close()
            first.close()

        # Loss of the local replica must not lose a confirmed remote commit.
        # Use a completely new replica path, not raw sqlite3 on an SDK-owned file.
        rebuilt = connect(root / "rebuilt.db")
        try:
            rebuilt.sync()
            assert scalar(rebuilt, "SELECT count(*) FROM probe") == 2
            assert scalar(rebuilt, "SELECT body FROM probe WHERE id=2") == "second fixture"
            report["rebuild_from_primary"] = True
            durations = []
            for _ in range(30):
                tick = time.perf_counter()
                assert scalar(rebuilt, "SELECT count(*) FROM probe_fts WHERE probe_fts MATCH 'aurora'") == 1
                durations.append((time.perf_counter() - tick) * 1000)
            report["local_read_mean_ms"] = round(sum(durations) / len(durations), 4)
        finally:
            rebuilt.close()
        report["replica_files_bytes"] = sum(p.stat().st_size for p in root.iterdir() if p.is_file())
    report["peak_process_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 3)
    print("PCS1_REPLICA_JSON=" + json.dumps(report, sort_keys=True), flush=True)
    # Mandatory primitives only. Missing DB-API features are adapter work, not
    # proof that the native driver is unsuitable.
    if not report["begin_immediate_rollback"] or not report["unique_constraint"]:
        raise SystemExit("PCS1_REPLICA_TRANSACTION_CONTRACT_FAILED")


if __name__ == "__main__":
    main()

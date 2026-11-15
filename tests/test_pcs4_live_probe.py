"""PCS.4A diagnostic: exact ranking, resource bounds and no primary writes."""
import json
import sqlite3

import pytest

from amora.app import AmoraCore
from amora.events import PrivacyScope
from amora.storage import pcs4_probe as probe
from amora.storage.schema import LATEST_SCHEMA_VERSION
from remote_storage_contracts_v13_source import remote
from test_memory_v03 import important_candidate
from test_messaging_lifecycle_stability import _clock


@pytest.fixture
def seeded(remote, tmp_path, monkeypatch):
    backend, server = remote
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    core = AmoraCore.bootstrap(storage=backend, clock=_clock())
    try:
        for index in range(4):
            assert core.memory.remember(important_candidate(
                content=f"Projeto sintético de memória conversa item {index}.",
                privacy_scope=PrivacyScope.PUBLIC, remote_processing_allowed=True,
            )).memory
        assert core.memory.remember(important_candidate(
            content="private-marker-do-not-print", privacy_scope=PrivacyScope.PRIVATE,
            owner_entity_id="PERSON_BK",
        )).memory
    finally:
        core.close()
    server.calls.clear()
    yield backend, server
    assert not server.streams


def test_pcs4_live_read_only_probe_exact_ranking_and_cleanup(seeded, tmp_path):
    backend, server = seeded
    cache = tmp_path / "probe"
    report = probe.certify(backend, cache)
    assert report["status"] == "ok"
    assert report["schema_version"] == LATEST_SCHEMA_VERSION
    assert report["rank_equal"] == [True, True]
    assert all(count > 0 for count in report["hit_counts"])
    assert all(count > 0 for count in report["remote_rank_calls"])
    assert report["runtime_projection_enabled"] is False
    assert report["snapshot_bytes"] > 0
    assert "private-marker-do-not-print" not in json.dumps(report)
    assert not list(cache.iterdir())
    assert "BEGIN" in server.calls and "ROLLBACK" in server.calls
    assert not any(sql.strip().upper().startswith((
        "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "BEGIN IMMEDIATE", "COMMIT"
    )) for sql in server.calls)
    assert not server.streams
    with sqlite3.connect(server.path) as conn:
        assert conn.execute("SELECT SUM(access_count) FROM memory_records").fetchone()[0] == 0


def test_pcs4_budget_stops_before_content_fetch(seeded, tmp_path, monkeypatch):
    backend, server = seeded
    monkeypatch.setattr(probe, "MAX_BYTES", 1)
    cache = tmp_path / "probe"
    with pytest.raises(probe.ProbeLimit, match="SNAPSHOT_BUDGET"):
        probe.certify(backend, cache)
    assert not any(sql.startswith('SELECT rowid AS pcs4_rowid,* FROM "entities"') for sql in server.calls)
    assert not list(cache.iterdir())
    assert not server.streams


def test_pcs4_no_ram_headroom_skips_before_remote(seeded, tmp_path, monkeypatch):
    backend, server = seeded
    monkeypatch.setattr(probe, "resources", lambda _: {
        "disk_free_bytes": 10**9, "cgroup_current_bytes": 490_000_000,
        "cgroup_limit_bytes": 500_000_000,
    })
    with pytest.raises(probe.ProbeLimit, match="RAM_HEADROOM"):
        probe.certify(backend, tmp_path / "probe")
    assert not server.calls


def test_pcs4_reports_error_type_not_credentials(monkeypatch, caplog):
    monkeypatch.setenv("AMORA_STORAGE_BACKEND", "libsql")
    monkeypatch.setenv("AMORA_LIBSQL_URL", "https://database.example")
    monkeypatch.setenv("AMORA_SECRET_LIBSQL_AUTH_TOKEN", "secret-marker")
    def fail(*args):
        raise RuntimeError("secret-marker")
    monkeypatch.setattr(probe, "certify", fail)
    report = probe.run()
    assert report == {"status": "error", "exception_type": "RuntimeError",
                      "runtime_projection_enabled": False}
    assert "secret-marker" not in caplog.text


def test_pcs4_default_no_remote_probe(monkeypatch):
    monkeypatch.setenv("AMORA_STORAGE_BACKEND", "sqlite")
    report = probe.run()
    assert report["status"] == "skipped"
    assert report["reason"] == "REQUIRES_LIBSQL"


def test_pcs4_pagination_preserves_rank_and_rows(seeded, tmp_path, monkeypatch):
    backend, server = seeded
    monkeypatch.setattr(probe, "FETCH_BATCH", 2)
    report = probe.certify(backend, tmp_path / "probe")
    assert report["status"] == "ok"
    assert report["rank_equal"] == [True, True]
    assert any('FROM "memory_records" WHERE rowid > ?' in sql for sql in server.calls)

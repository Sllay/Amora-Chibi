"""PCS.4C final gate: real Core bootstrap, restart and safe subprocess config."""
from datetime import datetime, timezone
import json

import httpx
import pytest

from amora.storage import pcs4_restart as restart
from amora.storage import pcs4_recovery as recovery
from amora.storage.backend import SQLiteBackend
from amora.storage.remote import LibSQLBackend
from remote_storage_contracts_v13_source import HranaSQLite, remote
from test_pcs4_recovery import backup


def test_restored_core_reopens_and_ranks_exactly(backup, tmp_path, monkeypatch):
    server = HranaSQLite(tmp_path / "isolated-restart.db")
    backend = LibSQLBackend("https://restore.example", "test-only",
                            transport=httpx.MockTransport(server))
    recovery.certify_restore(backup, backend)
    boots = []

    def boot(home, timestamp, backend=None):
        # Real Core, in-process only to retain the fixture's HTTP transport.
        with monkeypatch.context() as env:
            env.setenv("AMORA_HOME", str(home))
            env.setenv("AMORA_MODE", "local")
            env.setenv("AMORA_ASSET_STORAGE", "database")
            boots.append(backend is not None)
            return restart._boot(backend or SQLiteBackend(), timestamp)

    monkeypatch.setattr(restart, "_run_boot", boot)
    report = restart.certify_restart(backup, backend)
    assert boots == [False, True, True]
    assert report["rank_equal"] is True
    assert report["boots"] == 2 and report["query_count"] == 5
    assert report["private_hits"] > 0
    assert report["nonremote_hits"] > 0
    assert "fingerprints" not in report
    assert not server.streams


def test_unrestored_destination_never_bootstrapped(backup, tmp_path, monkeypatch):
    server = HranaSQLite(tmp_path / "empty.db")
    backend = LibSQLBackend("https://restore.example", "test-only",
                            transport=httpx.MockTransport(server))
    def unexpected(*args):
        pytest.fail("must not boot a nonmatching destination")
    monkeypatch.setattr(restart, "_run_boot", unexpected)
    with pytest.raises(Exception):
        restart.certify_restart(backup, backend)


def test_child_environment_does_not_inherit_source_or_providers(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_SECRET_LIBSQL_AUTH_TOKEN", "source-token")
    monkeypatch.setenv("AMORA_SECRET_GROQ_API_KEY", "provider-token")
    monkeypatch.setenv("GROQ_API_KEY", "other-provider-token")
    backend = LibSQLBackend("https://restore.example", "destination-token")
    env = restart._child_env(tmp_path, backend)
    assert env["AMORA_SECRET_LIBSQL_AUTH_TOKEN"] == "destination-token"
    assert env["AMORA_LIBSQL_URL"] == "https://restore.example"
    assert "AMORA_SECRET_GROQ_API_KEY" not in env and "GROQ_API_KEY" not in env
    assert "source-token" not in env.values()


def test_local_child_real_process(backup, tmp_path):
    home = tmp_path / "child"
    (home / "data").mkdir(parents=True)
    (home / "data" / "amora.db").write_bytes(backup.read_bytes())
    report = restart._run_boot(home, datetime(2026, 11, 15, tzinfo=timezone.utc))
    assert report["private_hits"] > 0
    assert len(report["fingerprints"]) == 5


def test_cli_sanitizes_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(restart, "destination_from_environment", lambda: object())
    def fail(*args):
        raise RuntimeError("private-token")
    monkeypatch.setattr(restart, "certify_restart", fail)
    assert restart.main([str(tmp_path / "private-path.db"), "--isolated-restored-destination"]) == 1
    output = capsys.readouterr().out
    assert "private-token" not in output and "private-path" not in output
    assert json.loads(output.split(" ", 1)[1])["status"] == "error"

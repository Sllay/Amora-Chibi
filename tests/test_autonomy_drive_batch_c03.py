"""C03: one atomic write for the complete seven-drive snapshot."""
from datetime import timedelta

import httpx
import pytest

from amora.app import AmoraCore
from amora.latency_trace import bind_latency_trace, install_remote_storage_probe
from amora.storage.backend import SQLiteBackend
from amora.storage.remote import LibSQLBackend
from test_messaging_lifecycle_stability import _clock
from test_remote_storage import HranaSQLite


@pytest.mark.parametrize("remote", [False, True])
def test_drive_snapshot_preserves_values_and_reduces_remote_calls(tmp_path, monkeypatch, remote):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    server = HranaSQLite(tmp_path / "remote.db") if remote else None
    requests = []

    def transport(request):
        requests.append(request)
        return server(request)

    backend = (LibSQLBackend("https://database.example", "test-only",
                             transport=httpx.MockTransport(transport))
               if remote else SQLiteBackend())
    clock = _clock()
    core = AmoraCore.bootstrap(clock=clock, storage=backend)
    try:
        before = core.autonomy.store.list_drives()
        assert len(before) == 7
        clock.advance(timedelta(hours=2))
        now = clock.now()
        expected = [state.model_copy(update={
            "need": min(1.0, max(0.0, state.need + state.growth_per_hour * 2)),
            "updated_at": now,
        }) for state in before]
        requests.clear()
        install_remote_storage_probe()
        with bind_latency_trace() as trace:
            result = core.autonomy.current_drives()
        assert result == expected
        assert core.autonomy.store.list_drives() == expected
        assert trace["stage_autonomy_drive_write_calls"] == 1
        if remote:
            assert trace["remote_sql_calls"] == 2
            assert trace["stage_autonomy_drive_read_sql_calls"] == 1
            assert trace["stage_autonomy_drive_write_sql_calls"] == 1
            assert len(requests) == 3  # measured read + write, then verification read
        else:
            assert trace.get("remote_sql_calls", 0) == 0

        # A single adjustment still uses the same persistence API.
        replacement = expected[0].model_copy(update={"need": 0.9})
        core.autonomy.store.put_drive(replacement)
        assert core.autonomy.store.get_drive(replacement.drive) == replacement
    finally:
        core.close()
    if remote:
        assert not server.streams

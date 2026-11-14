from datetime import timedelta

import httpx
import pytest

from amora.app import AmoraCore
from amora.latency_trace import bind_latency_trace, install_remote_storage_probe
from amora.storage.backend import SQLiteBackend
from amora.storage.remote import LibSQLBackend
from test_messaging_lifecycle_stability import _clock
from test_memory_v03 import important_candidate
from test_remote_storage import HranaSQLite


@pytest.mark.parametrize("remote", [False, True])
def test_memory_access_increment_atomic_and_persistent(tmp_path, monkeypatch, remote):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    server = HranaSQLite(tmp_path / "remote.db") if remote else None
    storage = LibSQLBackend("https://database.example", "test-only",
                           transport=httpx.MockTransport(server)) if remote else SQLiteBackend()
    clock = _clock()
    core = AmoraCore.bootstrap(clock=clock, storage=storage)
    try:
        first = core.memory.remember(important_candidate()).memory
        second = core.memory.remember(important_candidate().model_copy(
            update={"content": "um segundo desenho diferente", "summary": "segundo desenho"})).memory
        assert first is not None and second is not None
        assert first.id != second.id
        before = [core.memory.store.get(item.id) for item in (first, second)]
        clock.advance(timedelta(minutes=1))
        now = clock.now()
        install_remote_storage_probe()
        with bind_latency_trace() as trace:
            core.memory.store._mark_accessed([str(first.id), str(second.id)], now)
        assert trace["stage_memory_access_write_calls"] == 1
        if remote:
            assert trace["remote_sql_calls"] == 1
            assert trace["stage_memory_access_write_sql_calls"] == 1
        after = [core.memory.store.get(item.id) for item in (first, second)]
        for old, new in zip(before, after):
            assert new.access_count == old.access_count + 1
            assert new.last_accessed_at == now
            assert new.content == old.content
            assert new.tags == old.tags
            assert new.entity_refs == old.entity_refs
        with bind_latency_trace() as empty:
            core.memory.store._mark_accessed([], now)
        assert empty.get("remote_sql_calls", 0) == 0
    finally:
        core.close()
    if remote:
        assert not server.streams

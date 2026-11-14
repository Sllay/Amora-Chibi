import httpx
import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest
from amora.events import PrivacyScope
from amora.latency_trace import bind_latency_trace, install_remote_storage_probe
from amora.storage.backend import SQLiteBackend
from amora.storage.remote import LibSQLBackend
from test_messaging_lifecycle_stability import _clock
from test_remote_storage import HranaSQLite


@pytest.mark.asyncio
@pytest.mark.parametrize("remote", [False, True])
async def test_text_segment_metadata_is_persisted_on_initial_insert(tmp_path, monkeypatch, remote):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    server = HranaSQLite(tmp_path / "remote.db") if remote else None
    storage = LibSQLBackend("https://database.example", "test-only",
                           transport=httpx.MockTransport(server)) if remote else SQLiteBackend()
    core = AmoraCore.bootstrap(clock=_clock(), storage=storage)
    try:
        request = CognitiveRequest(
            text="Oi", speaker_id="PERSON_BK", target_id="ENTITY_AMORA",
            session_id="c04-private", privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
            metadata={"surface": "platform_messaging", "channel_type": "dm",
                      "test_mode": True, "memory_consolidation": False,
                      "relationship_learning": False, "emotion_persistence": False},
        )
        install_remote_storage_probe()
        with bind_latency_trace() as trace:
            speech = await core.cognition._emit_segment(request, "Resposta de teste.", 0,
                                                       "fixture", "fixture", None)
        assert speech == "Resposta de teste."
        if remote:
            assert trace["remote_sql_calls"] == 3
            assert trace.get("stage_segments_read_sql_calls", 0) == 0
        row = core.cognition.store.get_message_by_dedupe(f"cognitive:{request.turn_id}:0")
        assert row is not None
        assert row.raw_text == "Resposta de teste."
        assert row.metadata["test_mode"] is True
        for key in ("memory_consolidation", "relationship_learning", "emotion_persistence"):
            assert row.metadata[key] is False
        # Existing segment/row conflicts must still repair metadata without duplicates.
        request.metadata["test_mode"] = False
        await core.cognition._emit_segment(request, "Resposta de teste.", 0, "fixture", "fixture", None)
        persisted = core.cognition.store.get_message_by_dedupe(f"cognitive:{request.turn_id}:0")
        assert persisted.id == row.id
        assert persisted.metadata["test_mode"] is False
        assert len(core.cognition.store.list_segments(request.turn_id)) == 1
        assert core.cognition.store.message_count() == 1
    finally:
        core.close()
    if remote:
        assert not server.streams


@pytest.mark.asyncio
async def test_voice_callback_order_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "runtime"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        request = CognitiveRequest(text="Oi", speaker_id="PERSON_BK",
                                  target_id="ENTITY_AMORA",
                                  metadata={"surface": "web", "test_mode": True})
        observed = []

        async def callback(segment):
            row = core.cognition.store.get_message_by_dedupe(f"cognitive:{request.turn_id}:0")
            assert row is not None
            observed.append(row.metadata.get("test_mode"))

        await core.cognition._emit_segment(request, "Resposta.", 0, "fixture", "fixture", callback)
        assert observed == [None]
        assert core.cognition.store.get_message_by_dedupe(
            f"cognitive:{request.turn_id}:0").metadata["test_mode"] is True
    finally:
        core.close()

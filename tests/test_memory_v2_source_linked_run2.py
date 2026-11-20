"""Run 2: a fact's provenance can recover the conversation it came from."""
from __future__ import annotations

from uuid import uuid4
import pytest

from amora.app import AmoraCore
from amora.events import PrivacyScope
from amora.memory import MemoryCandidate, MemoryKind, MemoryProvenance, ProvenanceKind
from amora.storage.postgres_runtime_cognition import OWNER_EPISODE_SOURCES
from amora.storage.postgres_runtime_registry import compiled_domain_registry
from amora.time import SimulatedClock
from test_platform_messaging_step2 import _bind_bk
from test_persistent_owner_recall import _dm
from test_memory_v2_timeline_run1 import insert_turn, SOURCE, ANSWER


@pytest.mark.asyncio
async def test_named_owner_recall_includes_original_utterance_and_sent_amora_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    first = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(first)
        source_turn = insert_turn(first)
        saved = first.memory.remember(MemoryCandidate(
            kind=MemoryKind.EPISODIC,
            content="Usuário relatou que Orquídea terá cinco mapas.",
            summary="Projeto Orquídea: cinco mapas.",
            provenance=MemoryProvenance(
                kind=ProvenanceKind.INFERENCE,
                source="cognition:groq_memory", actor_id="ENTITY_AMORA",
                confidence=0.97,
            ),
            owner_entity_id="PERSON_BK", privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=False,
            importance=1, novelty=1, future_consequence=1, goal_relevance=1,
            metadata={"turn_id": str(source_turn)},
        ))
        assert saved.memory is not None
    finally:
        first.close()

    restarted = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(restarted)
        async def forbidden(*a, **k):
            pytest.fail("source-linked recall must be local, not a remote generation")
        monkeypatch.setattr(restarted.cognition, "respond", forbidden)
        monkeypatch.setattr(restarted.cognition, "reflect", forbidden)
        response = (await restarted.messaging.receive(
            _dm(restarted, "r2-recall", "Amora, lembra do projeto Orquídea?")
        )).outbound[0]
        assert response.metadata["cognitive_reason_codes"] == ["MEMORY_RECALL_PERSISTENT_FOUND"]
        assert "Usuário relatou que Orquídea terá cinco mapas" in response.text
        assert SOURCE in response.text
        assert ANSWER in response.text
        assert "11/11/2026" in response.text
        assert "No histórico original:" in response.text
    finally:
        restarted.close()


@pytest.mark.asyncio
async def test_untrusted_turn_id_or_missing_sent_reply_cannot_fabricate_own_words(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        from amora.messaging.models import OutboundStatus
        turn = insert_turn(core, reply="PRIVATE_DRAFT", status=OutboundStatus.QUEUED)
        episodes = core.cognition.store.owner_turn_conversations(
            "PERSON_BK", [str(turn), "not-a-uuid", str(uuid4())],
        )
        assert len(episodes) == 1
        assert episodes[str(turn)]["reply_text"] is None
        assert "PRIVATE_DRAFT" not in repr(episodes)
        assert not core.cognition.store.owner_turn_conversations("OTHER_OWNER", [str(turn)])
    finally:
        core.close()


def test_postgres_source_turn_query_is_reviewed_and_owner_scoped():
    approved = compiled_domain_registry()
    sql = approved[OWNER_EPISODE_SOURCES].postgres_sql
    assert approved[OWNER_EPISODE_SOURCES].parameter_count == 2
    assert approved[OWNER_EPISODE_SOURCES].max_result_rows == 6
    for clause in ("speaker_id=%s", "privacy_scope IN ('PERSONAL','PRIVATE')",
                   "memory_reflection_invalidations", "jsonb_array_elements_text(%s::jsonb)"):
        assert clause in sql
    assert "julianday(" not in sql and "json_extract(" not in sql

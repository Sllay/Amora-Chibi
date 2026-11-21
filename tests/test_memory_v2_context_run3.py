"""Run 3: budgeted ordinary dialogue context keeps actual topic continuity."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from amora.cognition import CognitiveRequest
from amora.cognition.models import ContextMemory
from amora.cognition.context_budget import estimate_payload
from amora.memory.context_selection import (
    is_social_only, subject_terms, select_memories, select_recent,
    measured_projection_chars,
)
from amora.app import AmoraCore
from amora.events import PrivacyScope
from amora.memory import MemoryCandidate, MemoryKind, MemoryProvenance, ProvenanceKind
from amora.time import SimulatedClock
from test_memory_v2_timeline_run1 import insert_turn, SOURCE, ANSWER
from test_platform_messaging_step2 import _bind_bk


def _record(mid, content):
    return ContextMemory(
        id=str(mid), kind="episodic", content=content,
        score=1.0, confidence=.95, privacy_scope="PERSONAL",
    )


def _history():
    rows = []
    for index in range(28):
        turn = str(UUID(int=index + 1))
        label = ("Orquídea: cinco mapas, nossa decisão antiga"
                 if index in {3, 7} else f"histórico genérico {index}")
        rows.extend([
            {"turn_id": turn, "speaker_id": "PERSON_BK", "text": label + " x" * 260},
            {"turn_id": turn, "speaker_id": "ENTITY_AMORA",
             "text": "Amora respondeu: " + label + " y" * 240},
        ])
    return rows


def test_context_policy_removes_irrelevant_memory_from_greeting():
    assert is_social_only("Oii!!")
    assert is_social_only("Ksksks")
    assert not subject_terms("Oi!")
    memories = [_record(i, "Projeto Orquídea com cinco mapas " + "m" * 200) for i in range(8)]
    before = _history()
    after, older = select_recent(before, "Oi!!")
    assert older == 0 and after == before[-8:]
    assert select_memories(memories, "Oi!!") == []
    assert measured_projection_chars([], after) < measured_projection_chars(memories, before) / 4


def test_relevant_old_conversation_keeps_both_user_and_amora_words():
    conversation = _history()
    recent, picked = select_recent(conversation, "E como ficou o Orquídea?")
    assert picked == 4  # two matching historical turns, with both speakers
    assert recent[-10:] == conversation[-10:]
    historic = [x for x in recent if x["turn_id"] in {str(UUID(int=4)), str(UUID(int=8))}]
    assert len(historic) == 4
    assert len(recent) == 14
    assert [m["speaker_id"] for m in historic] == [
        "PERSON_BK", "ENTITY_AMORA", "PERSON_BK", "ENTITY_AMORA",
    ]
    assert len({i for i in map(id, recent)}) == len(recent)


def test_relevant_memory_details_preserved_with_bounded_fallback():
    memories = [
        _record(1, "O usuário gosta de maracujá"),
        _record(2, "Projeto Orquídea tem cinco mapas e água volumétrica"),
        _record(3, "Projeto Orquídea terá animais passivos e ecossistema"),
        _record(4, "Um fato sem relação"),
        _record(5, "Uma notícia sem relação"),
    ]
    selected = select_memories(memories, "E o projeto Orquídea?")
    assert [item.id for item in selected[:2]] == ["2", "3"]
    assert len(selected) <= 3
    assert len(select_memories(memories, "Quando chega a atualização de um universo desconhecido?")) == 2
    assert len(select_memories(memories, "Okay!")) == 0


def test_provider_projection_has_smaller_envelope_than_old_full_history():
    history = _history()
    memories = [_record(n, f"Memória aleatória de projeto {n}" + "z" * 400) for n in range(8)]
    old = {"memories": [m.model_dump() for m in memories], "recent": history}
    recent, _ = select_recent(history, "Oii!")
    new = {"memories": [m.model_dump() for m in select_memories(memories, "Oii!")],
           "recent": recent}
    assert estimate_payload(new) < estimate_payload(old) * .35


def test_normal_private_dialogue_brings_original_remote_allowed_source(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        turn = insert_turn(core)
        memory = core.memory.remember(MemoryCandidate(
            kind=MemoryKind.EPISODIC, content="Projeto Orquídea terá cinco mapas.",
            summary="Orquídea, projeto de sobrevivência.",
            provenance=MemoryProvenance(
                kind=ProvenanceKind.USER_ASSERTION, source="test",
                actor_id="PERSON_BK", confidence=1.0),
            owner_entity_id="PERSON_BK",
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True, importance=1, novelty=1,
            goal_relevance=1, future_consequence=1,
            metadata={"turn_id": str(turn)},
        ))
        assert memory.memory
        request = CognitiveRequest(
            text="E como ficou o projeto Orquídea?",
            speaker_id="PERSON_BK", target_id="ENTITY_AMORA",
            session_id="new-private-session",
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
            metadata={"surface": "platform_messaging", "channel_type": "dm",
                      "cognitive_mode": "reply"},
        )
        context = core.cognition.compiler.compile(request)
        related = [item for item in context.memories if "Orquídea" in item.content]
        assert related and any(SOURCE in m.content for m in related)
        assert any(ANSWER in m.content for m in related)
        assert "MEMORY_V2_SOURCE_LINKED_EVIDENCE" in context.reason_codes
        assert "MEMORY_V2_BOUNDED_CONTEXT" in context.reason_codes
        # Unrelated greetings must not pack an encyclopedic memory dump.
        greeting = core.cognition.compiler.compile(
            request.model_copy(update={"text": "Oii!"}),
        )
        assert greeting.memories == []
        assert greeting.user_text == "Oii!"
        assert greeting.reason_codes.count("MEMORY_V2_BOUNDED_CONTEXT") == 1
        # Withheld consent means zero remote projection of source memory.
        local_only = core.cognition.compiler.compile(
            request.model_copy(update={"remote_processing_allowed": False}),
        )
        assert "MEMORY_V2_SOURCE_LINKED_EVIDENCE" not in local_only.reason_codes
    finally:
        core.close()


def test_short_acknowledgement_plus_continuation_keeps_recent_context_not_random_memories():
    history = _history()
    irrelevant_memories = [
        _record(1, "Uma receita de pão"),
        _record(2, "Um problema de astronomia"),
    ]
    for utterance in (
        "Certo, continue.", "Okay, prossiga", "Tá, pode continuar",
        "Sim, segue!", "Continua!", "Blz, pode seguir",
    ):
        assert is_social_only(utterance), utterance
        assert subject_terms(utterance) == frozenset()
        assert select_memories(irrelevant_memories, utterance) == []
        selected, historic = select_recent(history, utterance)
        assert selected == history[-8:]
        assert historic == 0
    # Specific action is NOT a mere acknowledgement; keep the actual topic.
    assert not is_social_only("Certo, continue a animação do coelho")
    assert "coelho" in subject_terms("Certo, continue a animação do coelho")

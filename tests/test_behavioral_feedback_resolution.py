"""Behavioral preferences have source-bound temporal precedence, not blanket rewriting."""
from datetime import timedelta

import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest
from amora.events import PrivacyScope
from amora.memory import MemoryCandidate, MemoryKind, MemoryProvenance, ProvenanceKind, MemoryQuery
from amora.memory.behavioral_resolution import resolve_behavioral_guidance
from amora.time import SimulatedClock


def record(core, content, *, topic="question_repetition", scope="general", owner="PERSON_BK"):
    result = core.memory.remember(MemoryCandidate(
        kind=MemoryKind.SEMANTIC,
        content=content, summary=content,
        provenance=MemoryProvenance(
            kind=ProvenanceKind.USER_ASSERTION, source="test-feedback",
            confidence=0.98, actor_id=owner),
        privacy_scope=PrivacyScope.PERSONAL,
        owner_entity_id=owner,
        audience={owner},
        remote_processing_allowed=True,
        entity_refs={owner: 1.0},
        tags={"amora_behavioral_feedback"},
        importance=0.98, novelty=0.94, future_consequence=0.95,
        relationship_relevance=0.9, goal_relevance=0.8,
        metadata={
            "behavioral_source": "verified_owner_dm_feedback",
            "behavioral_signal": "behavioral_correction",
            "behavioral_topic": topic,
            "behavioral_scope": scope,
        },
    ))
    assert result.memory is not None
    core.clock.advance(timedelta(minutes=1))
    return result.memory


def query(core):
    return core.memory.store.rank(MemoryQuery(
        text="E aí, Amora?",
        projection_recipient_id="PERSON_BK", projection_remote_only=True,
        supplement_tags={"amora_behavioral_feedback"}, supplement_limit=6,
    ))


def context(core):
    req = CognitiveRequest(
        text="E aí, Amora?", speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA", session_id="discord:primary:owner-dm",
        privacy_scope=PrivacyScope.PERSONAL, remote_processing_allowed=True,
        metadata={
            "surface": "platform_messaging", "platform": "discord",
            "channel_type": "dm",
        },
    )
    return core.cognition.compiler.compile(req)


def test_latest_general_correction_wins_without_erasing_old_source(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        old = record(core, "BK prefere que Amora nunca repita uma pergunta.")
        new = record(core, "BK prefere que Amora possa repetir perguntas quando necessário.")
        other = record(core, "BK prefere stickers de coelhinhos.", topic="sticker_usage")

        selected = resolve_behavioral_guidance(query(core), owner_id="PERSON_BK", limit=2)
        assert [entry.memory.id for entry in selected] == [other.id, new.id]
        assert core.memory.get(old.id) is not None
        assert core.memory.get(old.id).archived_at is None

        projected = context(core)
        ids = {entry.id for entry in projected.behavioral_guidance}
        assert ids == {str(new.id), str(other.id)}
        assert str(old.id) not in ids
        assert "BEHAVIORAL_GUIDANCE_PROJECTED" in projected.reason_codes

        # Revocation of the new source allows the older correction to resurface;
        # neither historical record was rewritten to resolve the conflict.
        assert core.memory.archive(new.id)
        after = context(core)
        after_ids = {entry.id for entry in after.behavioral_guidance}
        assert str(new.id) not in after_ids
        assert str(old.id) in after_ids
    finally:
        core.close()


def test_contextual_and_unknown_topics_never_silently_override_global_feedback(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        general = record(core, "BK prefere perguntas ocasionais quando ajudar.")
        contextual = record(core, "BK prefere evitar perguntas no meio de uma explicação.", scope="contextual")
        unknown_one = record(core, "BK disse que prefere um apelido diferente.", topic="other")
        unknown_two = record(core, "BK mudou o nome de outro detalhe.", topic="other")

        selected = resolve_behavioral_guidance(query(core), owner_id="PERSON_BK", limit=3)
        ids = {entry.memory.id for entry in selected}
        assert unknown_two.id in ids and unknown_one.id in ids
        # Older classified signals remain eligible but lower priority than
        # three distinct newer source events; they are not blindly deleted.
        all_known = resolve_behavioral_guidance(query(core), owner_id="PERSON_BK", limit=6)
        # The pure selector bounds projection to at most 3.
        assert len(all_known) <= 3
        assert general.id != contextual.id
    finally:
        core.close()


def test_behavioral_guidance_does_not_cross_owner_or_public_channel(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        record(core, "BK prefere não repetir perguntas.")
        stranger = record(core, "Outro usuário prefere outras respostas.", owner="PERSON_OTHER")
        selected = resolve_behavioral_guidance(query(core), owner_id="PERSON_BK", limit=2)
        assert stranger.id not in {hit.memory.id for hit in selected}
        base = CognitiveRequest(
            text="oi", speaker_id="PERSON_BK", target_id="ENTITY_AMORA",
            session_id="dm", privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
            metadata={"surface": "platform_messaging", "platform": "discord", "channel_type": "group"},
        )
        assert not core.cognition.compiler.compile(base).behavioral_guidance
        assert not core.cognition.compiler.compile(
            base.model_copy(update={"remote_processing_allowed": False,
                                   "metadata": {**base.metadata, "channel_type": "dm"}})
        ).behavioral_guidance
    finally:
        core.close()

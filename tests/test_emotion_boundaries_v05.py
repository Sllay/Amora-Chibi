from datetime import datetime, timezone

import pytest

from amora.app import AmoraCore
from amora.emotion import AppraisalInput
from amora.events import AmoraEvent
from amora.time import SimulatedClock


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 8, 23, 0, tzinfo=timezone.utc))


@pytest.mark.asyncio
async def test_affect_does_not_mutate_relationship_identity_or_revision_history(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        registry_before = tuple(
            (item.source, item.relation_type, item.target, item.confidence)
            for item in core.identities.relationships
        )
        revisions_before = tuple(
            (item.id, item.source_id, item.relation_type, item.target_id, item.confidence)
            for item in core.relationships.history("PERSON_BK", target_id="ENTITY_AMORA")
        )

        result = await core.emotion.process(
            AppraisalInput(
                description="BK cruzou um limite",
                outcome_valence=-0.95,
                target_id="PERSON_BK",
                agent_id="PERSON_BK",
                goal_relevance=0.9,
                agency=1.0,
                control=0.7,
                intentionality=1.0,
                norm_violation=1.0,
                relationship_relevance=1.0,
            )
        )
        assert result.target_affect is not None
        assert result.target_affect.anger > 0.5

        registry_after = tuple(
            (item.source, item.relation_type, item.target, item.confidence)
            for item in core.identities.relationships
        )
        revisions_after = tuple(
            (item.id, item.source_id, item.relation_type, item.target_id, item.confidence)
            for item in core.relationships.history("PERSON_BK", target_id="ENTITY_AMORA")
        )
        assert registry_after == registry_before
        assert revisions_after == revisions_before
    finally:
        core.close()


@pytest.mark.asyncio
async def test_generated_event_preserves_causal_ids_without_raw_description(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    captured: list[AmoraEvent] = []

    async def capture(event: AmoraEvent) -> None:
        if event.event_type == "emotion.generated":
            captured.append(event)

    await core.events.subscribe("emotion.generated", capture)
    try:
        cause = AmoraEvent(
            event_type="social.boundary_crossed",
            source="pytest",
            actor="PERSON_BK",
            payload={"private_text": "conteúdo que não deve ser copiado para emotion.generated"},
        )
        await core.events.publish(cause)
        result = await core.emotion.process_event(
            cause,
            outcome_valence=-0.85,
            goal_relevance=0.9,
            novelty=0.4,
            expectedness=0.5,
            agency=0.95,
            control=0.7,
            intentionality=0.95,
            norm_violation=0.9,
            relationship_relevance=0.9,
        )
        assert result.event is not None
        assert len(captured) == 1
        generated = captured[0]
        assert generated.causation_id == cause.id
        assert generated.causal_parent == cause.id
        assert generated.target == "PERSON_BK"
        assert generated.payload["emotion_event_id"] == str(result.event.id)
        assert "private_text" not in generated.payload
        assert "description" not in generated.payload
        assert "conteúdo" not in str(generated.payload)
    finally:
        await core.events.unsubscribe("emotion.generated", capture)
        core.close()

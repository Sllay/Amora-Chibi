from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest, JournalStage, ProviderDelta
from amora.events import PrivacyScope
from amora.models import ProviderClass, ProviderDescriptor, TaskClass
from amora.time import SimulatedClock


class PerspectiveProposalProvider:
    def __init__(
        self,
        *,
        subject_id: str | None,
        predicate: str = "likes",
        object_value=True,
        confidence: float = 0.86,
    ) -> None:
        self.subject_id = subject_id
        self.predicate = predicate
        self.object_value = object_value
        self.confidence = confidence
        self.descriptor = ProviderDescriptor(
            name="perspective-test",
            model="perspective-test-model",
            capabilities=frozenset({TaskClass.SOCIAL_DIALOGUE, TaskClass.REASONING}),
            quality=1.0,
            latency_score=1.0,
            cost_score=0.0,
            provider_class=ProviderClass.REMOTE_EPHEMERAL,
            allowed_privacy_scopes=frozenset({PrivacyScope.PERSONAL}),
            supports_streaming=True,
            supports_structured_output=True,
            context_window_tokens=100_000,
            priority=100,
        )

    async def stream(self, context):
        yield ProviderDelta(text="ok")
        yield ProviderDelta(done=True)

    async def propose(self, context):
        return {
            "memory_candidates": [],
            "belief_hypotheses": [
                {
                    "statement": "hipótese estruturada para teste de perspectiva",
                    "subject_id": self.subject_id,
                    "predicate": self.predicate,
                    "object_value": self.object_value,
                    "confidence": self.confidence,
                    "context_key": "perspective-runtime-test",
                }
            ],
            "action_suggestions": [],
            "tone": {},
        }


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc))


def _request(*, source_event_id=None) -> CognitiveRequest:
    return CognitiveRequest(
        text="Eu estou te contando isso agora.",
        speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA",
        source_event_id=source_event_id,
        privacy_scope=PrivacyScope.PERSONAL,
        remote_processing_allowed=True,
    )


@pytest.mark.asyncio
async def test_reflection_hypothesis_uses_perspective_consolidator_with_provenance(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        core.cognition.register_provider(
            PerspectiveProposalProvider(subject_id="PERSON_BK", object_value="café")
        )
        event_id = uuid4()
        request = _request(source_event_id=event_id)
        core.cognition.observe_input(request)

        bundle = await core.cognition.reflect(request)

        assert len(bundle.hypothesis_ids) == 1
        assert "PERSPECTIVE_HYPOTHESIS_CONSOLIDATED" in bundle.reason_codes
        claim = core.world_store.get_claim(bundle.hypothesis_ids[0])
        assert claim is not None
        assert claim.subject_id == "PERSON_BK"
        assert claim.object_value == "café"
        assert claim.source == "cognition:perspective-test"
        assert claim.source_entity_id == "ENTITY_AMORA"
        assert claim.metadata["perspective_consolidated"] is True
        assert claim.metadata["acir_proposition_type"] == "belief_hypothesis"
        assert claim.metadata["turn_id"] == str(request.turn_id)
        assert [item.ref_id for item in claim.evidence] == [str(event_id)]

        gated = [
            item
            for item in core.cognition.store.journal_for_turn(request.turn_id)
            if item.stage == JournalStage.PROPOSALS_GATED
        ][-1]
        assert gated.payload["deferred_hypothesis_count"] == 0
    finally:
        core.close()


@pytest.mark.asyncio
async def test_unknown_subject_is_deferred_instead_of_persisted_without_subject(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        core.cognition.register_provider(
            PerspectiveProposalProvider(subject_id="ENTITY_INVENTADA")
        )
        request = _request(source_event_id=uuid4())
        core.cognition.observe_input(request)
        before = core.world_store.claim_count()

        bundle = await core.cognition.reflect(request)

        assert bundle.hypothesis_ids == []
        assert "PERSPECTIVE_HYPOTHESIS_DEFERRED_SUBJECT" in bundle.reason_codes
        assert core.world_store.claim_count() == before
    finally:
        core.close()


@pytest.mark.asyncio
async def test_registered_third_party_outside_input_perspective_is_deferred(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        assert core.identities.get("ENTITY_CHATGPT") is not None
        core.cognition.register_provider(
            PerspectiveProposalProvider(subject_id="ENTITY_CHATGPT")
        )
        request = _request(source_event_id=uuid4())
        core.cognition.observe_input(request)

        bundle = await core.cognition.reflect(request)

        assert bundle.hypothesis_ids == []
        assert "PERSPECTIVE_HYPOTHESIS_OUTSIDE_INPUT_ACIR" in bundle.reason_codes
    finally:
        core.close()


@pytest.mark.asyncio
async def test_perspective_consolidation_feeds_corroborated_relationship_learning(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        core.cognition.register_provider(
            PerspectiveProposalProvider(
                subject_id="ENTITY_AMORA",
                predicate="child_of",
                object_value="PERSON_BK",
                confidence=0.88,
            )
        )

        first = _request(source_event_id=uuid4())
        core.cognition.observe_input(first)
        first_bundle = await core.cognition.reflect(first)
        assert len(first_bundle.hypothesis_ids) == 1
        assert core.identities.relationship("ENTITY_AMORA", "child_of", "PERSON_BK") is None

        second = _request(source_event_id=uuid4())
        core.cognition.observe_input(second)
        second_bundle = await core.cognition.reflect(second)
        assert len(second_bundle.hypothesis_ids) == 1

        learned = core.identities.relationship("ENTITY_AMORA", "child_of", "PERSON_BK")
        assert learned is not None
        relationship_state = core.world_store.current_relationship(
            "ENTITY_AMORA", "child_of", "PERSON_BK"
        )
        assert relationship_state is not None
        assert relationship_state.source_of_truth == "cognition:corroborated_relationship"
    finally:
        core.close()

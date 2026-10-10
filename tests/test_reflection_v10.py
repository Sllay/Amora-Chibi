from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest, JournalStage, ProviderDelta
from amora.events import PrivacyScope
from amora.models import ProviderClass, ProviderDescriptor, TaskClass
from amora.time import SimulatedClock
from amora.world import EpistemicKind


class StructuredProvider:
    def __init__(self) -> None:
        self.descriptor = ProviderDescriptor(
            name="structured-provider",
            model="structured-model",
            capabilities=frozenset({TaskClass.SOCIAL_DIALOGUE, TaskClass.REASONING}),
            quality=1.0,
            latency_score=1.0,
            cost_score=0.0,
            provider_class=ProviderClass.REMOTE_EPHEMERAL,
            allowed_privacy_scopes=frozenset({PrivacyScope.PERSONAL}),
            supports_streaming=True,
            supports_structured_output=True,
            context_window_tokens=100_000,
            priority=90,
        )

    async def stream(self, context):
        yield ProviderDelta(text="Tá bom, papis. Eu vou lembrar do que importa.")
        yield ProviderDelta(done=True, input_tokens=20, output_tokens=12, latency_ms=4.0, status_code=200)

    async def propose(self, context):
        assert "COGNITIVE_REFLECTION" in context.reason_codes
        return {
            "memory_candidates": [
                {
                    "kind": "semantic",
                    "content": "BK está trabalhando em um jogo novo.",
                    "summary": "BK trabalha em um jogo novo",
                    "confidence": 0.95,
                    "importance": 0.95,
                    "novelty": 0.95,
                    "emotional_weight": 0.7,
                    "relationship_relevance": 0.9,
                    "future_consequence": 0.8,
                    "goal_relevance": 0.8,
                    "entity_refs": {"PERSON_BK": 1.0, "ENTITY_INVENTADA": 1.0},
                    "tags": ["projeto"],
                }
            ],
            "belief_hypotheses": [
                {
                    "statement": "BK talvez queira mostrar o jogo quando estiver pronto.",
                    "subject_id": "PERSON_BK",
                    "predicate": "may_show_new_game",
                    "object_value": True,
                    "confidence": 0.72,
                    "context_key": "project",
                }
            ],
            "action_suggestions": [
                {
                    "action_kind": "initiate_contact",
                    "target_id": "PERSON_BK",
                    "urgency": 0.35,
                    "confidence": 0.65,
                    "reason_code": "SOCIAL_FOLLOWUP_CANDIDATE",
                }
            ],
            "tone": {"warmth": 0.8},
            "_usage": {"input_tokens": 120, "output_tokens": 55, "status_code": 200},
        }


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 10, 3, 0, tzinfo=timezone.utc))


@pytest.mark.asyncio
async def test_reflection_proposals_pass_through_local_gates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        core.cognition.register_provider(StructuredProvider())
        request = CognitiveRequest(
            text="Tô trabalhando num jogo novo e depois te mostro.",
            speaker_id="PERSON_BK",
            target_id="ENTITY_AMORA",
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
        )
        bundle = await core.cognition.reflect(request)

        assert len(bundle.accepted_memory_ids) == 1
        memory = core.memory.get(bundle.accepted_memory_ids[0])
        assert memory is not None
        assert memory.remote_processing_allowed is False
        assert "ENTITY_INVENTADA" not in memory.entity_refs
        assert memory.provenance.source == "cognition:structured-provider"

        assert len(bundle.hypothesis_ids) == 1
        hypothesis = core.world_store.get_claim(bundle.hypothesis_ids[0])
        assert hypothesis is not None
        assert hypothesis.kind == EpistemicKind.HYPOTHESIS
        assert hypothesis.canonical is False
        assert hypothesis.valid_until is not None
        assert hypothesis.remote_processing_allowed is False

        assert bundle.deferred_action_count == 1
        stages = [item.stage for item in core.cognition.store.journal_for_turn(request.turn_id)]
        assert JournalStage.REFLECTION_STARTED in stages
        assert JournalStage.PROPOSALS_GENERATED in stages
        assert JournalStage.PROPOSALS_GATED in stages
    finally:
        core.close()


@pytest.mark.asyncio
async def test_direct_dialogue_runs_reflection_after_streaming_plan(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        core.cognition.register_provider(StructuredProvider())
        decision, utterance = await core.conversation.observe_completed_utterance(
            "Tô trabalhando num jogo novo e depois te mostro.",
            speaker_id="PERSON_BK",
            addressee_id="ENTITY_AMORA",
            source_event_id=None,
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
        )
        assert utterance is not None
        turn_id = utterance.metadata.get("cognitive_turn_id")
        assert turn_id
        journal = core.cognition.store.journal_for_turn(turn_id)
        assert any(item.stage == JournalStage.PROPOSALS_GATED for item in journal)
        assert core.memory.store.count() >= 1
    finally:
        core.close()

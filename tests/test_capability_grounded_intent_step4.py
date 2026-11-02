from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from amora.actions import PluginRegistry
from amora.actions.builtin_capabilities import (
    DISCORD_EXPRESSION_CAPABILITY_ID,
    register_builtin_capabilities,
)
from amora.app import AmoraCore
from amora.autonomy import DecisionContext, DesireKind
from amora.cognition.capability_intent import CapabilityIntentGate
from amora.cognition.models import CognitiveContext, CognitiveRequest, ContextCapability
from amora.events import AmoraEvent, PrivacyScope
from amora.time import SimulatedClock


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc))


def _capability(*, availability: str = "available") -> ContextCapability:
    return ContextCapability(
        capability_id=DISCORD_EXPRESSION_CAPABILITY_ID,
        semantic_action="express_visually",
        summary="Express a Discord reaction using a transport-native visual expression.",
        affordances=["visual_reaction", "sticker"],
        availability=availability,
        preconditions=["current_transport_is_discord", "target_conversation_resolved"],
        effects=["visual_expression_delivered"],
        verification_effect="message_delivered",
    )


def _context(*capabilities: ContextCapability) -> CognitiveContext:
    return CognitiveContext(
        turn_id=uuid4(),
        created_at=datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc),
        speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA",
        user_text="manda uma figurinha engraçada",
        capabilities=list(capabilities),
    )


def test_builtin_discord_expression_is_known_but_unavailable_without_executor() -> None:
    plugins = PluginRegistry()
    register_builtin_capabilities(plugins.capability_registry)

    snapshot = plugins.capability_registry.snapshot(DISCORD_EXPRESSION_CAPABILITY_ID)
    assert snapshot is not None
    assert snapshot.availability.value == "unavailable"

    projection = snapshot.cognitive_projection()
    serialized = str(projection)
    assert projection["semantic_action"] == "express_visually"
    assert "plugin_id" not in serialized
    assert "tool_name" not in serialized
    assert "permissions" not in serialized
    assert "discord:expression:send" not in serialized


def test_generic_plugin_registry_does_not_get_production_builtins_implicitly() -> None:
    plugins = PluginRegistry()
    assert plugins.capability_registry.snapshot(DISCORD_EXPRESSION_CAPABILITY_ID) is None


def test_available_projected_capability_is_deferred_to_bdi_on_matching_transport() -> None:
    decision = CapabilityIntentGate.evaluate(
        "express_visually",
        _context(_capability(availability="available")),
        request_metadata={"platform": "discord"},
    )

    assert decision.matched is True
    assert decision.allowed_kind is True
    assert decision.capability_id == DISCORD_EXPRESSION_CAPABILITY_ID
    assert decision.decision == "deferred_to_bdi"
    assert "MODEL_HAS_NO_EXECUTION_AUTHORITY" in decision.reason_codes


def test_unavailable_projected_capability_stays_non_executable() -> None:
    decision = CapabilityIntentGate.evaluate(
        "express_visually",
        _context(_capability(availability="unavailable")),
        request_metadata={"platform": "discord"},
    )

    assert decision.matched is True
    assert decision.allowed_kind is False
    assert decision.decision == "deferred_capability_unavailable"
    assert "CAPABILITY_UNAVAILABLE" in decision.reason_codes


def test_transport_mismatch_rejects_discord_capability_locally() -> None:
    decision = CapabilityIntentGate.evaluate(
        "express_visually",
        _context(_capability()),
        request_metadata={"platform": "telegram"},
    )

    assert decision.matched is True
    assert decision.allowed_kind is False
    assert decision.decision == "rejected_by_local_gate"
    assert "CAPABILITY_TRANSPORT_MISMATCH" in decision.reason_codes


def test_missing_transport_context_rejects_transport_bound_capability() -> None:
    decision = CapabilityIntentGate.evaluate(
        "express_visually",
        _context(_capability()),
        request_metadata={},
    )

    assert decision.matched is True
    assert decision.allowed_kind is False
    assert "CAPABILITY_TRANSPORT_CONTEXT_MISSING" in decision.reason_codes


def test_unprojected_or_ambiguous_semantic_actions_never_gain_authority() -> None:
    missing = CapabilityIntentGate.evaluate(
        "invented_native_tool",
        _context(_capability()),
        request_metadata={"platform": "discord"},
    )
    assert missing.matched is False
    assert missing.allowed_kind is False
    assert missing.reason_codes == ["CAPABILITY_ACTION_NOT_PROJECTED"]

    duplicate = _capability().model_copy(update={"capability_id": "discord.expression.send.alt"})
    ambiguous = CapabilityIntentGate.evaluate(
        "express_visually",
        _context(_capability(), duplicate),
        request_metadata={"platform": "discord"},
    )
    assert ambiguous.matched is True
    assert ambiguous.allowed_kind is False
    assert "CAPABILITY_ACTION_AMBIGUOUS" in ambiguous.reason_codes


def test_real_core_projects_builtin_expression_without_exposing_binding(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        snapshot = core.actions.registry.capability_registry.snapshot(DISCORD_EXPRESSION_CAPABILITY_ID)
        assert snapshot is not None
        assert snapshot.availability.value == "unavailable"

        request = CognitiveRequest(
            text="manda uma figurinha engraçada",
            speaker_id="PERSON_BK",
            target_id="ENTITY_AMORA",
            metadata={
                "surface": "platform_messaging",
                "platform": "discord",
                "channel_type": "group",
            },
        )
        context = core.cognition.compiler.compile(request)
        projected = [item for item in context.capabilities if item.capability_id == DISCORD_EXPRESSION_CAPABILITY_ID]
        assert len(projected) == 1
        assert projected[0].semantic_action == "express_visually"
        assert projected[0].availability == "unavailable"

        serialized = context.model_dump_json()
        assert '"plugin_id"' not in serialized
        assert '"tool_name"' not in serialized
        assert "discord:expression:send" not in serialized
    finally:
        core.close()


@pytest.mark.asyncio
async def test_grounded_available_capability_becomes_bdi_candidate_not_direct_execution(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        event = AmoraEvent(
            event_type="cognition.action_suggested",
            timestamp=core.clock.now(),
            source="cognitive_engine:capability_grounding",
            actor="ENTITY_AMORA",
            target="PERSON_BK",
            confidence=0.91,
            importance=0.72,
            payload={
                "action_kind": "express_visually",
                "capability_id": DISCORD_EXPRESSION_CAPABILITY_ID,
                "capability_availability": "available",
                "capability_grounded": True,
                "allowed_kind": True,
                "reason_code": "CAPABILITY_GROUNDED_TO_TURN",
                "decision": "deferred_to_bdi",
                "model_has_execution_authority": False,
            },
            privacy_scope=PrivacyScope.PERSONAL,
        )
        await core.events.publish(event)

        assert core.autonomy.state()["pending_cognitive_suggestions"] == 1
        assert core.autonomy.store.current_intention() is None
        assert core.actions.store.plan_count() == 0
        assert core.actions.store.execution_count() == 0

        result = await core.autonomy.cycle(
            DecisionContext(is_idle=True, available_entities={"PERSON_BK"}),
            dedupe_key="step4-grounded-capability-cycle",
        )
        candidates = [item for item in result.candidates if item.source_kind == "cognitive_suggestion"]
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.kind == DesireKind.EXPRESS
        assert candidate.metadata["capability_id"] == DISCORD_EXPRESSION_CAPABILITY_ID
        assert candidate.metadata["capability_availability"] == "available"
        assert candidate.metadata["capability_grounded"] is True
        assert candidate.metadata["model_cannot_execute_directly"] is True
        assert candidate.metadata["utility_recomputed_locally"] is True
    finally:
        core.close()


@pytest.mark.asyncio
async def test_unavailable_grounded_capability_never_enters_bdi(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        await core.events.publish(
            AmoraEvent(
                event_type="cognition.action_suggested",
                timestamp=core.clock.now(),
                source="cognitive_engine:capability_grounding",
                actor="ENTITY_AMORA",
                target="PERSON_BK",
                confidence=0.9,
                importance=0.7,
                payload={
                    "action_kind": "express_visually",
                    "capability_id": DISCORD_EXPRESSION_CAPABILITY_ID,
                    "capability_availability": "unavailable",
                    "capability_grounded": True,
                    "allowed_kind": False,
                    "reason_code": "CAPABILITY_UNAVAILABLE",
                    "decision": "deferred_capability_unavailable",
                    "model_has_execution_authority": False,
                },
                privacy_scope=PrivacyScope.PERSONAL,
            )
        )
        assert core.autonomy.state()["pending_cognitive_suggestions"] == 0
        assert core.actions.store.plan_count() == 0
        assert core.actions.store.execution_count() == 0
    finally:
        core.close()

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from amora.app import AmoraCore
from amora.autonomy import DecisionContext, DesireKind
from amora.events import AmoraEvent, PrivacyScope
from amora.time import SimulatedClock


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 10, 7, 0, tzinfo=timezone.utc))


def _suggestion(core: AmoraCore, *, action_kind: str = "seek_repair", allowed: bool = True) -> AmoraEvent:
    return AmoraEvent(
        event_type="cognition.action_suggested",
        timestamp=core.clock.now(),
        source="cognitive_engine",
        actor="ENTITY_AMORA",
        target="PERSON_BK",
        confidence=0.95,
        importance=1.0,  # model may ask for max urgency; BDI must cap it locally
        payload={
            "action_kind": action_kind,
            "allowed_kind": allowed,
            "reason_code": "MODEL_TEST_SUGGESTION",
            "decision": "deferred_to_bdi" if allowed else "rejected_by_local_gate",
        },
        privacy_scope=PrivacyScope.PERSONAL,
    )


@pytest.mark.asyncio
async def test_cognitive_suggestion_becomes_local_utility_candidate_not_direct_intention(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        publish = await core.events.publish(_suggestion(core))
        assert publish.deduplicated is False
        assert core.autonomy.state()["pending_cognitive_suggestions"] == 1
        # Merely publishing the provider suggestion must not commit an intention.
        assert core.autonomy.store.current_intention() is None

        result = await core.autonomy.cycle(
            DecisionContext(is_idle=True, available_entities={"PERSON_BK"}),
            dedupe_key="v10-cognitive-suggestion-cycle",
        )
        candidates = [item for item in result.candidates if item.source_kind == "cognitive_suggestion"]
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.kind == DesireKind.SEEK_REPAIR
        assert candidate.urgency < core.autonomy.URGENT_SWITCH_THRESHOLD
        assert candidate.metadata["model_cannot_execute_directly"] is True
        assert candidate.metadata["utility_recomputed_locally"] is True
        assert candidate.factors.drive_need == pytest.approx(
            next(item.need for item in result.drives if item.drive == candidate.primary_drive)
        )
        # The exact semantic kind has a real Action Bus skill if BDI ever selects it.
        assert core.actions.skills.for_intention("seek_repair") is not None
    finally:
        core.close()


@pytest.mark.asyncio
async def test_rejected_or_expired_cognitive_suggestions_do_not_reach_bdi(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = _clock()
    core = AmoraCore.bootstrap(clock=clock)
    try:
        await core.events.publish(_suggestion(core, allowed=False))
        assert core.autonomy.state()["pending_cognitive_suggestions"] == 0

        await core.events.publish(_suggestion(core, action_kind="set_boundary", allowed=True))
        assert core.autonomy.state()["pending_cognitive_suggestions"] == 1
        clock.advance(timedelta(minutes=11))
        assert core.autonomy.state()["pending_cognitive_suggestions"] == 0

        result = await core.autonomy.cycle(
            DecisionContext(is_idle=True, available_entities={"PERSON_BK"}),
            dedupe_key="v10-expired-suggestion-cycle",
        )
        assert not any(item.source_kind == "cognitive_suggestion" for item in result.candidates)
        assert core.actions.skills.for_intention("set_boundary") is not None
        assert core.actions.skills.for_intention("wait") is not None
    finally:
        core.close()

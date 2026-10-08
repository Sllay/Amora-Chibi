from datetime import datetime, timezone

import pytest

from amora.app import AmoraCore
from amora.autonomy import DecisionContext
from amora.emotion import AppraisalInput, SocialBidStatus
from amora.time import SimulatedClock


@pytest.mark.asyncio
async def test_social_bid_is_consumed_only_after_verified_execution(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = SimulatedClock(datetime(2026, 10, 8, 23, 45, tzinfo=timezone.utc))
    core = AmoraCore.bootstrap(clock=clock)
    try:
        emotion = await core.emotion.process(
            AppraisalInput(
                description="BK ignorou Amora de propósito",
                outcome_valence=-0.9,
                target_id="PERSON_BK",
                agent_id="PERSON_BK",
                goal_relevance=0.9,
                novelty=0.4,
                expectedness=0.5,
                agency=0.95,
                control=0.6,
                intentionality=0.95,
                norm_violation=0.85,
                relationship_relevance=0.95,
                dedupe_key="v07-emotion-boundary",
            )
        )
        assert emotion.event is not None
        assert emotion.social_bids
        bid = emotion.social_bids[0]
        assert core.emotion.store.list_bids()[0].status == SocialBidStatus.PENDING

        decision = await core.autonomy.cycle(
            DecisionContext(is_idle=True, available_entities={"PERSON_BK"}),
            dedupe_key="v07-bid-cycle",
        )
        assert decision.intention is not None
        assert decision.intention.source_kind == "social_bid"
        assert decision.intention.source_ref_id == str(bid.id)
        assert core.emotion.store.list_bids()[0].status == SocialBidStatus.PENDING

        plan = await core.actions.plan_current(dedupe_key="v07-bid-plan")
        assert plan is not None
        assert plan.metadata["source_kind"] == "social_bid"
        assert plan.metadata["source_ref_id"] == str(bid.id)
        assert core.emotion.store.list_bids()[0].status == SocialBidStatus.PENDING

        result = await core.actions.execute_plan(plan.id, dedupe_key="v07-bid-execute")
        assert result.plan.status.value == "completed"
        assert core.emotion.store.list_bids() == []
        all_bids = core.emotion.store.list_bids(include_expired=True)
        stored = next(item for item in all_bids if item.id == bid.id)
        assert stored.status == SocialBidStatus.CONSUMED
    finally:
        core.close()

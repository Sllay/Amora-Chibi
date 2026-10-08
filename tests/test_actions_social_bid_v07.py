from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from amora.app import AmoraCore
from amora.autonomy import DecisionContext
from amora.emotion import SocialBid, SocialBidStatus, SocialBidType
from amora.time import SimulatedClock


@pytest.mark.asyncio
async def test_social_bid_is_consumed_only_after_verified_execution(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = SimulatedClock(datetime(2026, 10, 8, 23, 45, tzinfo=timezone.utc))
    core = AmoraCore.bootstrap(clock=clock)
    try:
        bid = SocialBid(
            bid_type=SocialBidType.SET_BOUNDARY,
            target_id="PERSON_BK",
            strength=0.95,
            reason="test boundary",
            source_emotion_event_id=uuid4(),
            created_at=clock.now(),
            expires_at=clock.now() + timedelta(minutes=45),
        )
        core.emotion.store.put_bid(bid)

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
        remaining = core.emotion.store.list_bids()
        assert remaining == []
        all_bids = core.emotion.store.list_bids(include_expired=True)
        stored = next(item for item in all_bids if item.id == bid.id)
        assert stored.status == SocialBidStatus.CONSUMED
    finally:
        core.close()

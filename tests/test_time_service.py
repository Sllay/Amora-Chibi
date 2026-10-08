from datetime import datetime, timedelta, timezone
from amora.time import Scheduler, SimulatedClock


async def test_scheduler_uses_simulated_clock() -> None:
    clock = SimulatedClock(datetime(2026, 10, 8, tzinfo=timezone.utc))
    scheduler = Scheduler(clock)
    calls: list[str] = []
    scheduler.schedule_once("wake", 60, lambda: calls.append("wake"))
    assert await scheduler.run_due() == []
    clock.advance(timedelta(seconds=60))
    assert await scheduler.run_due() == ["wake"]
    assert calls == ["wake"]

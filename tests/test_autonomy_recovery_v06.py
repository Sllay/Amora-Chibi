from datetime import datetime, timedelta, timezone

import pytest

from amora.app import AmoraCore
from amora.autonomy import AutonomyStore, DecisionContext, DecisionOutcome, DriveType
from amora.storage import AmoraDatabase
from amora.time import SimulatedClock


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 8, 23, 15, tzinfo=timezone.utc))


@pytest.mark.asyncio
async def test_intention_drives_and_decisions_survive_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); clock = _clock(); first = AmoraCore.bootstrap(clock=clock)
    try:
        adjusted, _ = first.autonomy.adjust_drive(DriveType.SOCIAL, 0.55, reason="long silence", dedupe_key="restart-drive-adjustment")
        result = await first.autonomy.cycle(DecisionContext(is_idle=True, available_entities={"PERSON_BK"}), dedupe_key="restart-cycle")
        assert result.decision.outcome == DecisionOutcome.SELECTED and result.intention is not None
        intention_id = result.intention.id; social_need = adjusted.need
        assert first.autonomy.store.decision_count() == 1
    finally: first.close()
    second = AmoraCore.bootstrap(clock=clock)
    try:
        current = second.autonomy.store.current_intention(); assert current is not None and current.id == intention_id
        drives = {item.drive: item.need for item in second.autonomy.current_drives()}; assert drives[DriveType.SOCIAL] == pytest.approx(social_need)
        assert second.autonomy.store.decision_count() == 1
        replay = await second.autonomy.cycle(DecisionContext(is_idle=True, available_entities={"PERSON_BK"}), dedupe_key="restart-cycle")
        assert replay.deduplicated is True and replay.intention is not None and replay.intention.id == intention_id
        assert second.autonomy.store.decision_count() == 1
    finally: second.close()


@pytest.mark.asyncio
async def test_backup_restore_preserves_autonomy_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); clock = _clock(); core = AmoraCore.bootstrap(clock=clock)
    try:
        core.autonomy.adjust_drive(DriveType.CURIOSITY, 0.60, reason="novel environment")
        result = await core.autonomy.cycle(DecisionContext(is_idle=True), dedupe_key="backup-cycle")
        assert result.intention is not None; intention_id = result.intention.id; backup = core.create_backup("v06-autonomy.db")
    finally: core.close()
    restored_path = AmoraDatabase.restore_backup(backup, tmp_path / "restored.db"); restored_db = AmoraDatabase(restored_path)
    try:
        assert restored_db.integrity_check() == "ok"; assert restored_db.schema_version() == 11
    finally: restored_db.close()
    store = AutonomyStore(restored_path, clock)
    try:
        assert store.decision_count() == 1
        current = store.current_intention(); assert current is not None and current.id == intention_id
        assert len(store.list_drives()) == 7
        replay = store.get_cycle_by_dedupe("backup-cycle"); assert replay is not None and replay.deduplicated is True
    finally: store.close()


@pytest.mark.asyncio
async def test_idle_scheduler_runs_repeated_cycles_only_when_idle(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); clock = _clock(); core = AmoraCore.bootstrap(clock=clock)
    try:
        core.autonomy.schedule_idle(core.scheduler, delay_seconds=60); clock.advance(timedelta(seconds=60))
        assert await core.scheduler.run_due(is_idle=False) == []; assert core.autonomy.store.decision_count() == 0
        clock.advance(timedelta(seconds=1)); assert await core.scheduler.run_due(is_idle=True) == ["autonomy.idle"]
        assert core.autonomy.store.decision_count() == 1
        clock.advance(timedelta(seconds=60)); assert await core.scheduler.run_due(is_idle=True) == ["autonomy.idle"]
        assert core.autonomy.store.decision_count() == 2
    finally: core.close()

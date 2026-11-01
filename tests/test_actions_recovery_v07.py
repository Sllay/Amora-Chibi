import asyncio
from pathlib import Path

from amora.actions import ActionStore
from amora.app import AmoraCore
from amora.autonomy import DecisionContext, DriveType
from amora.storage import AmoraDatabase
from amora.time import SystemClock


def _seed_and_execute(core: AmoraCore):
    core.autonomy.adjust_drive(DriveType.SOCIAL, 0.55, reason="recovery test", dedupe_key="recovery-v07-drive")
    decision = asyncio.run(core.autonomy.cycle(DecisionContext(is_idle=True, available_entities={"PERSON_BK"}), dedupe_key="recovery-v07-cycle"))
    assert decision.intention is not None
    result = asyncio.run(core.actions.execute_current(dedupe_key="recovery-v07-run")); assert result is not None; return result


def test_plan_and_verified_execution_survive_restart(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); first = AmoraCore.bootstrap()
    try:
        result = _seed_and_execute(first); plan_id = result.plan.id; execution_id = result.executions[0].id; assert first.actions.store.run_count() == 1
    finally: first.close()
    second = AmoraCore.bootstrap()
    try:
        plan = second.actions.store.get_plan(plan_id); assert plan is not None and plan.status.value == "completed"
        assert any(item.id == execution_id and item.verified for item in second.actions.store.list_executions())
        replay = second.actions.store.get_run_by_dedupe("recovery-v07-run"); assert replay is not None and replay.deduplicated is True and replay.plan.id == plan_id
    finally: second.close()


def test_backup_restore_preserves_action_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); core = AmoraCore.bootstrap()
    try: result = _seed_and_execute(core); backup = core.create_backup("v07-actions.db"); plan_id = result.plan.id
    finally: core.close()
    restored_path = AmoraDatabase.restore_backup(backup, tmp_path / "restored.db"); restored_db = AmoraDatabase(restored_path)
    try:
        assert restored_db.integrity_check() == "ok"
        assert restored_db.schema_version() == 14
    finally: restored_db.close()
    store = ActionStore(restored_path, SystemClock())
    try:
        plan = store.get_plan(plan_id); assert plan is not None and plan.status.value == "completed"
        assert store.run_count() == 1
        assert store.execution_count() == 1
    finally: store.close()

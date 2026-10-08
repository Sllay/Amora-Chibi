from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from uuid import UUID, uuid4

from amora.time import Clock

from .models import ActionPlan, PlanExecutionResult, PlanStatus, ToolExecutionResult


class ActionStore:
    def __init__(self, path: Path, clock: Clock) -> None:
        self.path = path
        self.clock = clock
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def put_plan(self, plan: ActionPlan) -> bool:
        payload = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    """
                    INSERT INTO action_plans(
                        id, intention_id, intention_kind, target_id, skill_name, planner_mode,
                        status, plan_json, created_at, updated_at, dedupe_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status=excluded.status,
                        plan_json=excluded.plan_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        str(plan.id), str(plan.intention_id), plan.intention_kind, plan.target_id,
                        plan.skill_name, plan.planner_mode.value, plan.status.value, payload,
                        plan.created_at.isoformat(), plan.updated_at.isoformat(), plan.dedupe_key,
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def get_plan(self, plan_id: UUID | str) -> ActionPlan | None:
        with self._lock:
            row = self._conn.execute("SELECT plan_json FROM action_plans WHERE id = ?", (str(plan_id),)).fetchone()
        return ActionPlan.model_validate(json.loads(row["plan_json"])) if row else None

    def get_plan_by_dedupe(self, dedupe_key: str) -> ActionPlan | None:
        with self._lock:
            row = self._conn.execute("SELECT plan_json FROM action_plans WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
        return ActionPlan.model_validate(json.loads(row["plan_json"])) if row else None

    def latest_for_intention(self, intention_id: UUID | str) -> ActionPlan | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT plan_json FROM action_plans WHERE intention_id = ? ORDER BY created_at DESC LIMIT 1",
                (str(intention_id),),
            ).fetchone()
        return ActionPlan.model_validate(json.loads(row["plan_json"])) if row else None

    def list_plans(self, limit: int = 50) -> list[ActionPlan]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT plan_json FROM action_plans ORDER BY created_at DESC LIMIT ?",
                (max(1, min(200, int(limit))),),
            ).fetchall()
        return [ActionPlan.model_validate(json.loads(row["plan_json"])) for row in rows]

    def plan_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM action_plans").fetchone()
        return int(row[0]) if row else 0

    def put_execution(self, item: ToolExecutionResult) -> None:
        payload = json.dumps(item.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO tool_executions(
                    id, call_id, plan_id, step_id, plugin_id, tool_name, status, attempt,
                    result_json, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(item.id), str(item.call_id), str(item.plan_id), str(item.step_id),
                    item.plugin_id, item.tool_name, item.status.value, item.attempt, payload,
                    item.started_at.isoformat(), item.finished_at.isoformat(),
                ),
            )

    def list_executions(self, limit: int = 100) -> list[ToolExecutionResult]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT result_json FROM tool_executions ORDER BY finished_at DESC LIMIT ?",
                (max(1, min(500, int(limit))),),
            ).fetchall()
        return [ToolExecutionResult.model_validate(json.loads(row["result_json"])) for row in rows]

    def execution_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM tool_executions").fetchone()
        return int(row[0]) if row else 0

    def put_run(self, result: PlanExecutionResult, *, dedupe_key: str | None) -> bool:
        now = self.clock.now()
        run_id = uuid4()
        status = result.plan.status.value
        if status not in {PlanStatus.COMPLETED.value, PlanStatus.FAILED.value, PlanStatus.ABORTED.value}:
            raise ValueError("action run may only persist a terminal plan result")
        payload = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    """
                    INSERT INTO action_runs(id, plan_id, status, result_json, created_at, updated_at, dedupe_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (str(run_id), str(result.plan.id), status, payload, now.isoformat(), now.isoformat(), dedupe_key),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def _hydrate_run(self, row: sqlite3.Row | None) -> PlanExecutionResult | None:
        if row is None:
            return None
        result = PlanExecutionResult.model_validate(json.loads(row["result_json"]))
        result.deduplicated = True
        for execution in result.executions:
            execution.deduplicated = True
        return result

    def get_run_by_dedupe(self, dedupe_key: str) -> PlanExecutionResult | None:
        with self._lock:
            row = self._conn.execute("SELECT result_json FROM action_runs WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
        return self._hydrate_run(row)

    def latest_run_for_plan(self, plan_id: UUID | str) -> PlanExecutionResult | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT result_json FROM action_runs WHERE plan_id = ? ORDER BY created_at DESC LIMIT 1",
                (str(plan_id),),
            ).fetchone()
        return self._hydrate_run(row)

    def run_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM action_runs").fetchone()
        return int(row[0]) if row else 0

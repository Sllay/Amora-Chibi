from __future__ import annotations


MIGRATION_V07: tuple[int, str] = (
    7,
    """
    CREATE TABLE IF NOT EXISTS action_plans (
        id TEXT PRIMARY KEY,
        intention_id TEXT NOT NULL REFERENCES autonomy_intentions(id) ON DELETE CASCADE,
        intention_kind TEXT NOT NULL,
        target_id TEXT,
        skill_name TEXT,
        planner_mode TEXT NOT NULL CHECK(planner_mode IN ('reflex','rule','htn','llm')),
        status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed','aborted')),
        plan_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        dedupe_key TEXT UNIQUE
    );
    CREATE INDEX IF NOT EXISTS idx_action_plans_intention ON action_plans(intention_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_action_plans_status ON action_plans(status, updated_at DESC);

    CREATE TABLE IF NOT EXISTS action_runs (
        id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL REFERENCES action_plans(id) ON DELETE CASCADE,
        status TEXT NOT NULL CHECK(status IN ('completed','failed','aborted')),
        result_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        dedupe_key TEXT UNIQUE
    );
    CREATE INDEX IF NOT EXISTS idx_action_runs_plan ON action_runs(plan_id, created_at DESC);

    CREATE TABLE IF NOT EXISTS tool_executions (
        id TEXT PRIMARY KEY,
        call_id TEXT NOT NULL,
        plan_id TEXT NOT NULL REFERENCES action_plans(id) ON DELETE CASCADE,
        step_id TEXT NOT NULL,
        plugin_id TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('succeeded','failed','denied','timed_out')),
        attempt INTEGER NOT NULL CHECK(attempt >= 1),
        result_json TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_tool_exec_plan_step ON tool_executions(plan_id, step_id, attempt);
    CREATE INDEX IF NOT EXISTS idx_tool_exec_time ON tool_executions(finished_at DESC);
    CREATE INDEX IF NOT EXISTS idx_tool_exec_plugin ON tool_executions(plugin_id, tool_name, finished_at DESC);
    """,
)

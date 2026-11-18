"""Explicit PostgreSQL statements for canonical ActionStore plan/run telemetry."""
from __future__ import annotations
from .postgres_runtime_connection import ReviewedStatement

def action_statements() -> dict[str,ReviewedStatement]:
    m:dict[str,ReviewedStatement]={}
    def add(src,pg,n,cap=1000):
        key=" ".join(src.split())
        if key in m:
            raise ValueError("PG_ACTION_DUPLICATE_SQL")
        m[key]=ReviewedStatement(pg,n,cap)

    add("""INSERT INTO action_plans(
        id, intention_id, intention_kind, target_id, skill_name, planner_mode,
        status, plan_json, created_at, updated_at, dedupe_key
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        status=excluded.status,
        plan_json=excluded.plan_json,
        updated_at=excluded.updated_at""",
        """INSERT INTO amora_pg_v18_lab.action_plans(
        id,intention_id,intention_kind,target_id,skill_name,planner_mode,
        status,plan_json,created_at,updated_at,dedupe_key)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(id) DO UPDATE SET
          status=EXCLUDED.status,plan_json=EXCLUDED.plan_json,
          updated_at=EXCLUDED.updated_at""",11)
    for source,pg,n,cap in [
        ("SELECT plan_json FROM action_plans WHERE id = ?",
         "SELECT plan_json FROM amora_pg_v18_lab.action_plans WHERE id=%s",1,1),
        ("SELECT plan_json FROM action_plans WHERE dedupe_key = ?",
         "SELECT plan_json FROM amora_pg_v18_lab.action_plans WHERE dedupe_key=%s",1,1),
        ("SELECT plan_json FROM action_plans WHERE intention_id = ? ORDER BY created_at DESC LIMIT 1",
         "SELECT plan_json FROM amora_pg_v18_lab.action_plans WHERE intention_id=%s ORDER BY created_at DESC LIMIT 1",1,1),
        ("SELECT plan_json FROM action_plans ORDER BY created_at DESC LIMIT ?",
         "SELECT plan_json FROM amora_pg_v18_lab.action_plans ORDER BY created_at DESC LIMIT %s",1,200),
        ("SELECT COUNT(*) FROM action_plans",
         "SELECT COUNT(*) FROM amora_pg_v18_lab.action_plans",0,1),
        ("SELECT result_json FROM tool_executions ORDER BY finished_at DESC LIMIT ?",
         "SELECT result_json FROM amora_pg_v18_lab.tool_executions ORDER BY finished_at DESC LIMIT %s",1,500),
        ("SELECT COUNT(*) FROM tool_executions",
         "SELECT COUNT(*) FROM amora_pg_v18_lab.tool_executions",0,1),
        ("SELECT result_json FROM action_runs WHERE dedupe_key = ?",
         "SELECT result_json FROM amora_pg_v18_lab.action_runs WHERE dedupe_key=%s",1,1),
        ("SELECT result_json FROM action_runs WHERE plan_id = ? ORDER BY created_at DESC LIMIT 1",
         "SELECT result_json FROM amora_pg_v18_lab.action_runs WHERE plan_id=%s ORDER BY created_at DESC LIMIT 1",1,1),
        ("SELECT COUNT(*) FROM action_runs",
         "SELECT COUNT(*) FROM amora_pg_v18_lab.action_runs",0,1),
    ]:
        add(source,pg,n,cap)
    add("""INSERT INTO tool_executions(
        id, call_id, plan_id, step_id, plugin_id, tool_name, status, attempt,
        result_json, started_at, finished_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        """INSERT INTO amora_pg_v18_lab.tool_executions(
        id,call_id,plan_id,step_id,plugin_id,tool_name,status,attempt,
        result_json,started_at,finished_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",11)
    add("""INSERT INTO action_runs(id, plan_id, status, result_json, created_at, updated_at, dedupe_key)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        """INSERT INTO amora_pg_v18_lab.action_runs(
        id,plan_id,status,result_json,created_at,updated_at,dedupe_key)
        VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",7)
    return m

"""Reviewed PostgreSQL autonomy, drive and intention SQL mappings."""
from __future__ import annotations
from .postgres_runtime_connection import ReviewedStatement

DRIVE_BATCH="PG_AUTONOMY_DRIVE_BATCH"

def autonomy_statements() -> dict[str,ReviewedStatement]:
    m:dict[str,ReviewedStatement]={}
    def add(src,pg,n,cap=1000,*,batch=False):
        key=" ".join(src.split())
        if key in m: raise ValueError("PG_AUTONOMY_DUPLICATE_SQL")
        m[key]=ReviewedStatement(pg,n,cap,batch)
    add("SELECT COUNT(*) FROM autonomy_decisions",
        "SELECT COUNT(*) FROM amora_pg_v18_lab.autonomy_decisions",0,1)
    add("""INSERT INTO autonomy_drives(drive, need, baseline, growth_per_hour, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(drive) DO NOTHING""",
        """INSERT INTO amora_pg_v18_lab.autonomy_drives(
        drive,need,baseline,growth_per_hour,updated_at)
        VALUES (%s,%s,%s,%s,%s) ON CONFLICT(drive) DO NOTHING""",5)
    add("SELECT * FROM autonomy_drives WHERE drive = ?",
        "SELECT * FROM amora_pg_v18_lab.autonomy_drives WHERE drive=%s",1,1)
    add("SELECT * FROM autonomy_drives ORDER BY drive",
        "SELECT * FROM amora_pg_v18_lab.autonomy_drives ORDER BY drive",0,100)
    add(DRIVE_BATCH,"""INSERT INTO amora_pg_v18_lab.autonomy_drives(
        drive,need,baseline,growth_per_hour,updated_at)
        VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT(drive) DO UPDATE SET
            need=EXCLUDED.need,baseline=EXCLUDED.baseline,
            growth_per_hour=EXCLUDED.growth_per_hour,
            updated_at=EXCLUDED.updated_at""",5,batch=True)
    add("""INSERT INTO drive_adjustments(id, drive, delta, reason, source_event_id, created_at, dedupe_key)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        """INSERT INTO amora_pg_v18_lab.drive_adjustments(
        id,drive,delta,reason,source_event_id,created_at,dedupe_key)
        VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",7)
    add("SELECT * FROM drive_adjustments WHERE dedupe_key = ?",
        "SELECT * FROM amora_pg_v18_lab.drive_adjustments WHERE dedupe_key=%s",1,1)
    add("UPDATE autonomy_intentions SET status = ?, updated_at = ? WHERE status = ? AND expires_at IS NOT NULL AND expires_at <= ?",
        """UPDATE amora_pg_v18_lab.autonomy_intentions
        SET status=%s,updated_at=%s WHERE status=%s
          AND expires_at IS NOT NULL AND expires_at<=%s""",4)
    add("SELECT * FROM autonomy_intentions WHERE status = ? ORDER BY created_at DESC LIMIT 1",
        """SELECT * FROM amora_pg_v18_lab.autonomy_intentions
        WHERE status=%s ORDER BY created_at DESC LIMIT 1""",1,1)
    add("""INSERT INTO autonomy_intentions(
        id, kind, target_id, source_kind, source_ref_id, selected_utility,
        urgency, commitment_strength, created_at, updated_at, expires_at,
        status, metadata_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        selected_utility=excluded.selected_utility,
        urgency=excluded.urgency,
        commitment_strength=excluded.commitment_strength,
        updated_at=excluded.updated_at,
        expires_at=excluded.expires_at,
        status=excluded.status,
        metadata_json=excluded.metadata_json""",
        """INSERT INTO amora_pg_v18_lab.autonomy_intentions(
        id,kind,target_id,source_kind,source_ref_id,selected_utility,
        urgency,commitment_strength,created_at,updated_at,expires_at,
        status,metadata_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(id) DO UPDATE SET
        selected_utility=EXCLUDED.selected_utility,urgency=EXCLUDED.urgency,
        commitment_strength=EXCLUDED.commitment_strength,
        updated_at=EXCLUDED.updated_at,expires_at=EXCLUDED.expires_at,
        status=EXCLUDED.status,metadata_json=EXCLUDED.metadata_json""",13)
    add("UPDATE autonomy_intentions SET status = ?, updated_at = ? WHERE id = ?",
        "UPDATE amora_pg_v18_lab.autonomy_intentions SET status=%s,updated_at=%s WHERE id=%s",3)
    add("""INSERT INTO autonomy_decisions(
        id, outcome, selected_intention_id, previous_intention_id,
        selected_utility, silence_utility, reason_codes_json,
        result_json, created_at, dedupe_key
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        """INSERT INTO amora_pg_v18_lab.autonomy_decisions(
        id,outcome,selected_intention_id,previous_intention_id,
        selected_utility,silence_utility,reason_codes_json,
        result_json,created_at,dedupe_key)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",10)
    add("SELECT result_json FROM autonomy_decisions WHERE dedupe_key = ?",
        "SELECT result_json FROM amora_pg_v18_lab.autonomy_decisions WHERE dedupe_key=%s",1,1)
    add("SELECT * FROM autonomy_decisions ORDER BY created_at DESC LIMIT ?",
        "SELECT * FROM amora_pg_v18_lab.autonomy_decisions ORDER BY created_at DESC LIMIT %s",1,200)
    return m

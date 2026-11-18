"""Reviewed PostgreSQL emotion, mood, affect and social bids SQL."""
from __future__ import annotations
from .postgres_runtime_connection import ReviewedStatement

EVENT_LIST="PG_EMOTION_EVENT_LIST"
BID_LIST="PG_EMOTION_BID_LIST"

def emotion_statements() -> dict[str,ReviewedStatement]:
    m:dict[str,ReviewedStatement]={}
    def add(src,pg,n,cap=1000):
        key=" ".join(src.split())
        if key in m: raise ValueError("PG_EMOTION_DUPLICATE_SQL")
        m[key]=ReviewedStatement(pg,n,cap)
    add("SELECT COUNT(*) FROM emotion_events",
        "SELECT COUNT(*) FROM amora_pg_v18_lab.emotion_events",0,1)
    add("""INSERT INTO emotion_events(
        id, emotion, intensity, cause_event_id, cause_description,
        target_id, agent_id, blame, intentionality, spillover,
        appraisal_json, privacy_scope, created_at, dedupe_key
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        """INSERT INTO amora_pg_v18_lab.emotion_events(
        id,emotion,intensity,cause_event_id,cause_description,target_id,
        agent_id,blame,intentionality,spillover,appraisal_json,
        privacy_scope,created_at,dedupe_key)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT DO NOTHING""",14)
    add("SELECT * FROM emotion_events WHERE id = ?",
        "SELECT * FROM amora_pg_v18_lab.emotion_events WHERE id=%s",1,1)
    add("SELECT * FROM emotion_events WHERE dedupe_key = ?",
        "SELECT * FROM amora_pg_v18_lab.emotion_events WHERE dedupe_key=%s",1,1)
    add(EVENT_LIST,"""SELECT * FROM amora_pg_v18_lab.emotion_events
        WHERE (%s::text IS NULL OR target_id=%s)
        ORDER BY created_at DESC LIMIT %s""",3,200)
    add("SELECT * FROM mood_state WHERE singleton_id = 1",
        "SELECT * FROM amora_pg_v18_lab.mood_state WHERE singleton_id=1",0,1)
    add("""INSERT INTO mood_state(singleton_id, valence, arousal, dominance, updated_at)
        VALUES (1, ?, ?, ?, ?)
        ON CONFLICT(singleton_id) DO UPDATE SET
            valence=excluded.valence,
            arousal=excluded.arousal,
            dominance=excluded.dominance,
            updated_at=excluded.updated_at""",
        """INSERT INTO amora_pg_v18_lab.mood_state(
        singleton_id,valence,arousal,dominance,updated_at)
        VALUES (1,%s,%s,%s,%s)
        ON CONFLICT(singleton_id) DO UPDATE SET
        valence=EXCLUDED.valence,arousal=EXCLUDED.arousal,
        dominance=EXCLUDED.dominance,updated_at=EXCLUDED.updated_at""",4)
    add("SELECT * FROM entity_affect WHERE entity_id = ?",
        "SELECT * FROM amora_pg_v18_lab.entity_affect WHERE entity_id=%s",1,1)
    add("""INSERT INTO entity_affect(
        entity_id, anger, affection, trust, irritation, fear, gratitude, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(entity_id) DO UPDATE SET
        anger=excluded.anger,
        affection=excluded.affection,
        trust=excluded.trust,
        irritation=excluded.irritation,
        fear=excluded.fear,
        gratitude=excluded.gratitude,
        updated_at=excluded.updated_at""",
        """INSERT INTO amora_pg_v18_lab.entity_affect(
        entity_id,anger,affection,trust,irritation,fear,gratitude,updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(entity_id) DO UPDATE SET
        anger=EXCLUDED.anger,affection=EXCLUDED.affection,trust=EXCLUDED.trust,
        irritation=EXCLUDED.irritation,fear=EXCLUDED.fear,
        gratitude=EXCLUDED.gratitude,updated_at=EXCLUDED.updated_at""",8)
    add("SELECT * FROM entity_affect ORDER BY entity_id",
        "SELECT * FROM amora_pg_v18_lab.entity_affect ORDER BY entity_id",0,10000)
    add("""INSERT INTO social_bids(
        id, bid_type, target_id, strength, reason, source_emotion_event_id,
        created_at, expires_at, status, metadata_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        """INSERT INTO amora_pg_v18_lab.social_bids(
        id,bid_type,target_id,strength,reason,source_emotion_event_id,
        created_at,expires_at,status,metadata_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",10)
    add("UPDATE social_bids SET status = ? WHERE status = ? AND expires_at <= ?",
        """UPDATE amora_pg_v18_lab.social_bids SET status=%s
        WHERE status=%s AND expires_at<=%s""",3)
    add(BID_LIST,"""SELECT * FROM amora_pg_v18_lab.social_bids
        WHERE (%s=1 OR status=%s) ORDER BY created_at DESC LIMIT %s""",3,200)
    add("UPDATE social_bids SET status = ? WHERE id = ?",
        "UPDATE amora_pg_v18_lab.social_bids SET status=%s WHERE id=%s",2)
    return m

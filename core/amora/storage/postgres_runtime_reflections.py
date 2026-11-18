"""Reviewed PostgreSQL reflection queue SQL for canonical ReflectionQueue.

Each key reproduces a single canonical SQLite statement (whitespace
normalized); every destination uses explicit schema-qualified PG18 SQL.
No SQL rewriting is performed during execution and no production backend is
activated. Queue claim uses optimistic CAS to avoid duplicate claims.
"""
from __future__ import annotations
from .postgres_runtime_connection import ReviewedStatement

def reflection_statements()->dict[str,ReviewedStatement]:
    r:dict[str,ReviewedStatement]={}
    def add(source,postgres,count,cap=1000):
        key=" ".join(source.split())
        if key in r:
            raise ValueError("PG_REFLECTION_DUPLICATE_SQL")
        r[key]=ReviewedStatement(postgres,count,cap)

    add(
        "INSERT INTO memory_reflection_jobs(turn_id,input_id,status,created_at,available_at) "
        "SELECT ?,?,'pending',?,? WHERE NOT EXISTS ("
        "SELECT 1 FROM memory_reflection_invalidations x WHERE x.session_id=? "
        "AND x.native_message_hash=? AND x.invalidated_through>=?) "
        "ON CONFLICT(turn_id) DO NOTHING",
        """INSERT INTO amora_pg_v18_lab.memory_reflection_jobs(
            turn_id,input_id,status,created_at,available_at)
        SELECT %s,%s,'pending',%s,%s WHERE NOT EXISTS(
            SELECT 1 FROM amora_pg_v18_lab.memory_reflection_invalidations x
            WHERE x.session_id=%s AND x.native_message_hash=%s
              AND x.invalidated_through>=%s
        ) ON CONFLICT(turn_id) DO NOTHING""",7)
    add("""INSERT INTO memory_reflection_jobs(turn_id,input_id,status,created_at,available_at)
        SELECT m.turn_id,m.id,'pending',m.started_at,?
        FROM conversation_messages m
        WHERE m.dedupe_key >= 'cognitive-input:'
          AND m.dedupe_key < 'cognitive-input;'
          AND m.dedupe_key='cognitive-input:' || m.turn_id
          AND m.remote_processing_allowed=1 AND m.privacy_scope NOT IN ('AUTH','SECRET')
          AND json_extract(m.metadata_json,'$.surface')='platform_messaging'
          AND json_extract(m.metadata_json,'$.reflection_enqueue_requested')=1
          AND COALESCE(json_extract(m.metadata_json,'$.test_mode'),0)=0
          AND COALESCE(json_extract(m.metadata_json,'$.memory_consolidation'),1)=1
          AND NOT EXISTS (SELECT 1 FROM memory_reflection_jobs j WHERE j.turn_id=m.turn_id)
          AND NOT EXISTS (SELECT 1 FROM memory_reflection_invalidations x
              WHERE x.session_id=m.session_id
              AND x.native_message_hash=json_extract(m.metadata_json,'$.native_message_id_hash')
              AND x.invalidated_through>=COALESCE(json_extract(m.metadata_json,'$.transport_revision'),1))
        ORDER BY m.started_at,m.turn_id LIMIT 32
        ON CONFLICT(turn_id) DO NOTHING""",
        """INSERT INTO amora_pg_v18_lab.memory_reflection_jobs(
            turn_id,input_id,status,created_at,available_at)
        SELECT m.turn_id,m.id,'pending',m.started_at,%s
        FROM amora_pg_v18_lab.conversation_messages m
        WHERE m.dedupe_key >= 'cognitive-input:'
          AND m.dedupe_key < 'cognitive-input;'
          AND m.dedupe_key='cognitive-input:' || m.turn_id
          AND m.remote_processing_allowed=1
          AND m.privacy_scope NOT IN ('AUTH','SECRET')
          AND m.metadata_json::jsonb ->> 'surface'='platform_messaging'
          AND m.metadata_json::jsonb ->> 'reflection_enqueue_requested' IN ('1','true')
          AND COALESCE(m.metadata_json::jsonb ->> 'test_mode','0') IN ('0','false')
          AND COALESCE(m.metadata_json::jsonb ->> 'memory_consolidation','1') IN ('1','true')
          AND NOT EXISTS (
              SELECT 1 FROM amora_pg_v18_lab.memory_reflection_jobs j
              WHERE j.turn_id=m.turn_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM amora_pg_v18_lab.memory_reflection_invalidations x
              WHERE x.session_id=m.session_id
                AND x.native_message_hash=m.metadata_json::jsonb ->> 'native_message_id_hash'
                AND x.invalidated_through>=COALESCE(
                    (m.metadata_json::jsonb ->> 'transport_revision')::BIGINT,1)
          )
        ORDER BY m.started_at,m.turn_id LIMIT 32
        ON CONFLICT(turn_id) DO NOTHING""",1)
    # Shared source_invalidated() is already reviewed and registered by
    # cognition_statements(); do not override the exact same SQL key.
    add(
        "INSERT INTO memory_reflection_invalidations(session_id,native_message_hash,invalidated_through) "
        "VALUES (?,?,?) ON CONFLICT(session_id,native_message_hash) DO UPDATE SET "
        "invalidated_through=MAX(invalidated_through,excluded.invalidated_through)",
        """INSERT INTO amora_pg_v18_lab.memory_reflection_invalidations(
            session_id,native_message_hash,invalidated_through)
        VALUES (%s,%s,%s)
        ON CONFLICT(session_id,native_message_hash) DO UPDATE SET
        invalidated_through=GREATEST(
          memory_reflection_invalidations.invalidated_through,
          EXCLUDED.invalidated_through
        )""",3)
    stale="""SELECT m.turn_id FROM conversation_messages m
        JOIN memory_reflection_invalidations x ON x.session_id=m.session_id
        AND x.native_message_hash=json_extract(m.metadata_json,'$.native_message_id_hash')
        WHERE x.session_id=? AND x.native_message_hash=?
        AND m.dedupe_key='cognitive-input:' || m.turn_id
        AND COALESCE(json_extract(m.metadata_json,'$.transport_revision'),1)<=x.invalidated_through"""
    pg_stale="""SELECT m.turn_id FROM amora_pg_v18_lab.conversation_messages m
        JOIN amora_pg_v18_lab.memory_reflection_invalidations x
          ON x.session_id=m.session_id
         AND x.native_message_hash=m.metadata_json::jsonb ->> 'native_message_id_hash'
        WHERE x.session_id=%s AND x.native_message_hash=%s
          AND m.dedupe_key='cognitive-input:' || m.turn_id
          AND COALESCE(
            (m.metadata_json::jsonb ->> 'transport_revision')::BIGINT,1
          )<=x.invalidated_through"""
    add(
        "UPDATE memory_reflection_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,"
        "last_reason='REFLECTION_SOURCE_INVALIDATED' WHERE status<>'cancelled' AND turn_id IN ("+stale+")",
        """UPDATE amora_pg_v18_lab.memory_reflection_jobs SET
            status='cancelled',lease_token=NULL,lease_until=NULL,
            last_reason='REFLECTION_SOURCE_INVALIDATED'
        WHERE status<>'cancelled' AND turn_id IN ("""+pg_stale+")",2)
    add(
        "UPDATE memory_records SET archived_at=?,updated_at=? WHERE archived_at IS NULL "
        "AND provenance_source LIKE 'cognition:%' AND json_extract(metadata_json,'$.turn_id') IN ("+stale+")",
        """UPDATE amora_pg_v18_lab.memory_records SET
            archived_at=%s,updated_at=%s WHERE archived_at IS NULL
            AND provenance_source LIKE 'cognition:%%'
            AND metadata_json::jsonb ->> 'turn_id' IN ("""+pg_stale+")",4)
    add(
        "UPDATE memory_reflection_jobs SET status='pending',attempts=0,available_at=?,"
        "lease_token=NULL,lease_until=NULL,last_reason='REFLECTION_OPERATOR_RETRY' "
        "WHERE turn_id=? AND status='failed'",
        """UPDATE amora_pg_v18_lab.memory_reflection_jobs
        SET status='pending',attempts=0,available_at=%s,lease_token=NULL,
            lease_until=NULL,last_reason='REFLECTION_OPERATOR_RETRY'
        WHERE turn_id=%s AND status='failed'""",2)
    add(
        "SELECT m.turn_id FROM conversation_messages m "
        "WHERE m.dedupe_key >= 'cognitive-input:' "
        "AND m.dedupe_key < 'cognitive-input;' "
        "AND m.dedupe_key='cognitive-input:' || m.turn_id "
        "AND m.privacy_scope='PERSONAL' AND m.remote_processing_allowed=1 "
        "AND m.speaker_id IS NOT NULL AND m.session_id IS NOT NULL "
        "AND json_extract(m.metadata_json,'$.surface')='platform_messaging' "
        "AND json_extract(m.metadata_json,'$.channel_type')='dm' "
        "AND COALESCE(json_extract(m.metadata_json,'$.test_mode'),0)=0 "
        "AND COALESCE(json_extract(m.metadata_json,'$.memory_consolidation'),1)=1 "
        "AND NOT EXISTS (SELECT 1 FROM memory_reflection_jobs j WHERE j.turn_id=m.turn_id) "
        "AND NOT EXISTS (SELECT 1 FROM cognitive_journal j WHERE j.turn_id=m.turn_id AND j.stage='proposals_gated') "
        "AND NOT EXISTS (SELECT 1 FROM memory_reflection_invalidations x WHERE x.session_id=m.session_id "
        "AND x.native_message_hash=json_extract(m.metadata_json,'$.native_message_id_hash') "
        "AND x.invalidated_through>=COALESCE(json_extract(m.metadata_json,'$.transport_revision'),1)) "
        "ORDER BY m.started_at DESC LIMIT 100",
        """SELECT m.turn_id FROM amora_pg_v18_lab.conversation_messages m
        WHERE m.dedupe_key>='cognitive-input:' AND m.dedupe_key<'cognitive-input;'
          AND m.dedupe_key='cognitive-input:' || m.turn_id
          AND m.privacy_scope='PERSONAL' AND m.remote_processing_allowed=1
          AND m.speaker_id IS NOT NULL AND m.session_id IS NOT NULL
          AND m.metadata_json::jsonb ->> 'surface'='platform_messaging'
          AND m.metadata_json::jsonb ->> 'channel_type'='dm'
          AND COALESCE(m.metadata_json::jsonb ->> 'test_mode','0') IN ('0','false')
          AND COALESCE(m.metadata_json::jsonb ->> 'memory_consolidation','1') IN ('1','true')
          AND NOT EXISTS (
              SELECT 1 FROM amora_pg_v18_lab.memory_reflection_jobs j
              WHERE j.turn_id=m.turn_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM amora_pg_v18_lab.cognitive_journal j
              WHERE j.turn_id=m.turn_id AND j.stage='proposals_gated'
          )
          AND NOT EXISTS (
              SELECT 1 FROM amora_pg_v18_lab.memory_reflection_invalidations x
              WHERE x.session_id=m.session_id
                AND x.native_message_hash=m.metadata_json::jsonb ->> 'native_message_id_hash'
                AND x.invalidated_through>=COALESCE(
                    (m.metadata_json::jsonb ->> 'transport_revision')::BIGINT,1
                )
          )
        ORDER BY m.started_at DESC LIMIT 100""",0,100)
    add(
        "SELECT turn_id,attempts,last_reason FROM memory_reflection_jobs "
        "WHERE status='failed' ORDER BY created_at DESC LIMIT ?",
        """SELECT turn_id,attempts,last_reason
        FROM amora_pg_v18_lab.memory_reflection_jobs
        WHERE status='failed' ORDER BY created_at DESC LIMIT %s""",1,100)
    add(
        "UPDATE memory_reflection_jobs SET status='failed',lease_token=NULL,lease_until=NULL,"
        "last_reason='REFLECTION_ATTEMPTS_EXHAUSTED' WHERE status='claimed' AND lease_until<=? AND attempts>=?",
        """UPDATE amora_pg_v18_lab.memory_reflection_jobs
        SET status='failed',lease_token=NULL,lease_until=NULL,
            last_reason='REFLECTION_ATTEMPTS_EXHAUSTED'
        WHERE status='claimed' AND lease_until<=%s AND attempts>=%s""",2)
    add(
        "SELECT turn_id,input_id,attempts FROM memory_reflection_jobs "
        "WHERE attempts<? AND ((status='pending' AND available_at<=?) "
        "OR (status='claimed' AND lease_until<=?)) "
        "ORDER BY available_at,created_at,turn_id LIMIT 1",
        """SELECT turn_id,input_id,attempts
        FROM amora_pg_v18_lab.memory_reflection_jobs
        WHERE attempts<%s AND (
          (status='pending' AND available_at<=%s)
          OR (status='claimed' AND lease_until<=%s)
        ) ORDER BY available_at,created_at,turn_id LIMIT 1""",3,1)
    add(
        "UPDATE memory_reflection_jobs SET status='claimed',attempts=attempts+1,"
        "lease_token=?,lease_until=? WHERE turn_id=? AND attempts=? AND attempts<? AND "
        "((status='pending' AND available_at<=?) OR (status='claimed' AND lease_until<=?))",
        """UPDATE amora_pg_v18_lab.memory_reflection_jobs
        SET status='claimed',attempts=attempts+1,lease_token=%s,lease_until=%s
        WHERE turn_id=%s AND attempts=%s AND attempts<%s
          AND ((status='pending' AND available_at<=%s)
               OR (status='claimed' AND lease_until<=%s))""",7)
    add(
        "UPDATE memory_reflection_jobs SET status=?,available_at=?,lease_token=NULL,lease_until=NULL,"
        "last_reason=? WHERE turn_id=? AND status='claimed' AND lease_token=? AND lease_until>?",
        """UPDATE amora_pg_v18_lab.memory_reflection_jobs
        SET status=%s,available_at=%s,lease_token=NULL,lease_until=NULL,
            last_reason=%s WHERE turn_id=%s AND status='claimed'
            AND lease_token=%s AND lease_until>%s""",6)
    add(
        "UPDATE memory_reflection_jobs SET status='pending',attempts=MAX(0,attempts-1),"
        "available_at=?,lease_token=NULL,lease_until=NULL,last_reason='REFLECTION_PROVIDERS_IN_BACKOFF' "
        "WHERE turn_id=? AND status='claimed' AND lease_token=? AND lease_until>?",
        """UPDATE amora_pg_v18_lab.memory_reflection_jobs
        SET status='pending',attempts=GREATEST(0,attempts-1),
            available_at=%s,lease_token=NULL,lease_until=NULL,
            last_reason='REFLECTION_PROVIDERS_IN_BACKOFF'
        WHERE turn_id=%s AND status='claimed' AND lease_token=%s
          AND lease_until>%s""",4)
    add("SELECT status,attempts,last_reason FROM memory_reflection_jobs WHERE turn_id=?",
        """SELECT status,attempts,last_reason
        FROM amora_pg_v18_lab.memory_reflection_jobs WHERE turn_id=%s""",1,1)
    add("SELECT status, COUNT(*) AS total FROM memory_reflection_jobs GROUP BY status",
        """SELECT status,COUNT(*) AS total FROM amora_pg_v18_lab.memory_reflection_jobs
        GROUP BY status""",0,16)
    return r

"""Explicit PostgreSQL cognition / conversation history SQL registry (v18 lab).

The original CognitiveStore remains the domain authority. Dynamic history
filters are implemented by a dedicated SQL query with bound optional values.
No SQL string rewriting, automatic production switch or privileged fallback.
"""
from __future__ import annotations
from .postgres_runtime_connection import ReviewedStatement

HISTORY = "PG_COGNITION_SCOPED_HISTORY"
OWNER_DAY_INPUTS = "PG_COGNITION_OWNER_DAY_INPUTS"
OWNER_DAY_REPLIES = "PG_COGNITION_OWNER_DAY_REPLIES"
LATEST_USAGE = "PG_COGNITION_LATEST_USAGE"
MERGE_METADATA = "PG_COGNITION_MERGE_MESSAGE_METADATA"

def cognition_statements() -> dict[str,ReviewedStatement]:
    m: dict[str,ReviewedStatement] = {}
    def add(source: str, pg: str, params: int, cap: int=1000,
            *,batch:bool=False) -> None:
        key=" ".join(source.split())
        if key in m:
            raise ValueError("PG_COGNITION_DUPLICATE_QUERY")
        m[key]=ReviewedStatement(pg,params,cap,batch)

    insert_message="""INSERT INTO conversation_messages(
        id, turn_id, speaker_id, addressee_id, source, raw_text, normalized_text, spoken_text,
        privacy_scope, remote_processing_allowed, stt_confidence, provider, model, status, interrupted,
        source_event_id, session_id, started_at, finished_at, metadata_json, dedupe_key
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    add(insert_message,"""INSERT INTO amora_pg_v18_lab.conversation_messages(
        id,turn_id,speaker_id,addressee_id,source,raw_text,normalized_text,
        spoken_text,privacy_scope,remote_processing_allowed,stt_confidence,
        provider,model,status,interrupted,source_event_id,session_id,
        started_at,finished_at,metadata_json,dedupe_key)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT DO NOTHING""",21,batch=True)
    add("SELECT * FROM conversation_messages WHERE dedupe_key = ?",
        "SELECT * FROM amora_pg_v18_lab.conversation_messages WHERE dedupe_key=%s",1,1)
    add(HISTORY,"""
        SELECT cm.* FROM amora_pg_v18_lab.conversation_messages cm
        WHERE (%s::text IS NULL OR cm.speaker_id=%s)
          AND (%s::text IS NULL OR cm.turn_id=%s)
          AND (%s::text IS NULL OR cm.session_id=%s)
          AND (
            %s=0 OR (
                (
                  COALESCE(cm.metadata_json::jsonb ->> 'temporal_initiative','0')
                    NOT IN ('1','true')
                  OR (
                    cm.speaker_id='ENTITY_AMORA' AND EXISTS(
                      SELECT 1 FROM amora_pg_v18_lab.messaging_outbound_queue sent
                      WHERE sent.id=cm.turn_id AND sent.status='sent'
                    )
                  )
                )
                AND NOT EXISTS(
                    SELECT 1 FROM amora_pg_v18_lab.conversation_messages original
                    JOIN amora_pg_v18_lab.memory_reflection_invalidations x
                      ON x.session_id=original.session_id
                     AND x.native_message_hash=
                        original.metadata_json::jsonb ->> 'native_message_id_hash'
                    WHERE original.turn_id=cm.turn_id
                      AND original.dedupe_key='cognitive-input:' || original.turn_id
                      AND COALESCE(
                        (original.metadata_json::jsonb ->> 'transport_revision')::BIGINT,
                        1
                      )<=x.invalidated_through
                )
            )
          )
        ORDER BY cm.started_at DESC LIMIT %s
    """,8,500)
    # Historical owner recall: source scope and revision invalidations are
    # enforced *inside* the reviewed PG SQL, before returning any private text.
    # Use timestamptz casts and JSONB, never an unreviewed SQLite expression.
    add(OWNER_DAY_INPUTS, """
        SELECT m.* FROM amora_pg_v18_lab.conversation_messages m
        WHERE m.speaker_id=%s AND m.addressee_id='ENTITY_AMORA'
          AND m.dedupe_key='cognitive-input:' || m.turn_id
          AND m.session_id IS NOT NULL AND m.status='received'
          AND m.privacy_scope IN ('PERSONAL','PRIVATE')
          AND m.metadata_json::jsonb ->> 'surface'='platform_messaging'
          AND m.metadata_json::jsonb ->> 'platform'='discord'
          AND m.metadata_json::jsonb ->> 'channel_type'='dm'
          AND COALESCE(m.metadata_json::jsonb ->> 'test_mode','0') NOT IN ('1','true')
          AND COALESCE(m.metadata_json::jsonb ->> 'observed_only','0') NOT IN ('1','true')
          AND NOT EXISTS (
              SELECT 1 FROM amora_pg_v18_lab.memory_reflection_invalidations x
              WHERE x.session_id=m.session_id
                AND x.native_message_hash=m.metadata_json::jsonb ->> 'native_message_id_hash'
                AND x.invalidated_through>=COALESCE(
                    (m.metadata_json::jsonb ->> 'transport_revision')::bigint,1)
          )
          AND (
            (m.started_at::timestamptz >= %s::timestamptz
             AND m.started_at::timestamptz < %s::timestamptz)
            OR (NULLIF(m.metadata_json::jsonb ->> 'platform_sent_at','')::timestamptz >= %s::timestamptz
                AND NULLIF(m.metadata_json::jsonb ->> 'platform_sent_at','')::timestamptz < %s::timestamptz)
          )
        ORDER BY m.started_at::timestamptz,m.id LIMIT %s
    """,6,513)
    # UUIDs are read from validated internal turn identifiers and encoded into
    # JSON. A bound jsonb array is a single reviewed query, never dynamic SQL.
    add(OWNER_DAY_REPLIES, """
        SELECT account_scope,conversation_id,text,metadata_json
        FROM amora_pg_v18_lab.messaging_outbound_queue
        WHERE platform='discord' AND target_entity_id=%s AND status='sent'
          AND metadata_json::jsonb ->> 'cognitive_turn_id' IN (
              SELECT jsonb_array_elements_text(%s::jsonb))
        ORDER BY created_at,id
    """,2,64)

    add("SELECT 1 FROM memory_reflection_invalidations WHERE session_id=? AND native_message_hash=? AND invalidated_through>=?",
        """SELECT 1 FROM amora_pg_v18_lab.memory_reflection_invalidations
        WHERE session_id=%s AND native_message_hash=%s
          AND invalidated_through>=%s LIMIT 1""",3,1)
    add("SELECT 1 FROM memory_reflection_jobs WHERE turn_id=? AND status='claimed' AND lease_token=? AND lease_until>?",
        """SELECT 1 FROM amora_pg_v18_lab.memory_reflection_jobs
        WHERE turn_id=%s AND status='claimed'
          AND lease_token=%s AND lease_until>%s LIMIT 1""",3,1)
    add("SELECT privacy_scope FROM conversation_messages WHERE id = ?",
        "SELECT privacy_scope FROM amora_pg_v18_lab.conversation_messages WHERE id=%s FOR UPDATE",1,1)
    add("""UPDATE conversation_messages
        SET status = ?, spoken_text = COALESCE(?, spoken_text), interrupted = COALESCE(?, interrupted),
            finished_at = CASE WHEN ? IN ('spoken','interrupted','failed') THEN ? ELSE finished_at END
        WHERE id = ?""",
        """UPDATE amora_pg_v18_lab.conversation_messages SET
        status=%s,spoken_text=COALESCE(%s,spoken_text),
        interrupted=COALESCE(%s,interrupted),
        finished_at=CASE WHEN %s IN ('spoken','interrupted','failed') THEN %s ELSE finished_at END
        WHERE id=%s""",6)
    insert_journal="""INSERT INTO cognitive_journal(
        id, turn_id, stage, timestamp, source, summary, reason_codes_json, payload_json,
        privacy_scope, provider, model, duration_ms, correlation_id, error_code, metadata_json, dedupe_key
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(dedupe_key) DO NOTHING"""
    cols="""id,turn_id,stage,timestamp,source,summary,reason_codes_json,
        payload_json,privacy_scope,provider,model,duration_ms,correlation_id,
        error_code,metadata_json,dedupe_key"""
    add(insert_journal,"INSERT INTO amora_pg_v18_lab.cognitive_journal ("+cols+
        ") VALUES ("+",".join(["%s"]*16)+") ON CONFLICT(dedupe_key) DO NOTHING",
        16,batch=True)
    add("""INSERT INTO cognitive_journal(
        id, turn_id, stage, timestamp, source, summary, reason_codes_json, payload_json,
        privacy_scope, provider, model, duration_ms, correlation_id, error_code, metadata_json, dedupe_key
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?), (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(dedupe_key) DO NOTHING""",
        "INSERT INTO amora_pg_v18_lab.cognitive_journal ("+cols+") VALUES ("+
        ",".join(["%s"]*16)+"),("+",".join(["%s"]*16)+
        ") ON CONFLICT(dedupe_key) DO NOTHING",32)
    add("SELECT * FROM cognitive_journal WHERE turn_id = ? ORDER BY timestamp, rowid",
        # SQLite rowid is insertion-order and has no PG equivalent. This
        # deterministic ID tie-breaker is a candidate, not proven parity.
        "SELECT * FROM amora_pg_v18_lab.cognitive_journal WHERE turn_id=%s ORDER BY timestamp,id",1,10000)
    add("SELECT * FROM cognitive_journal ORDER BY timestamp DESC LIMIT ?",
        "SELECT * FROM amora_pg_v18_lab.cognitive_journal ORDER BY timestamp DESC LIMIT %s",1,1000)
    insert_segment="""INSERT INTO speech_segments(
        id, turn_id, ordinal, display_text, speech_text, status, tts_provider, voice_id,
        queued_at, started_at, finished_at, interrupted, metadata_json
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    add(insert_segment,"""INSERT INTO amora_pg_v18_lab.speech_segments(
        id,turn_id,ordinal,display_text,speech_text,status,tts_provider,voice_id,
        queued_at,started_at,finished_at,interrupted,metadata_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT DO NOTHING""",13,batch=True)
    add("SELECT 1 FROM speech_segments WHERE turn_id=? UNION ALL SELECT 1 FROM conversation_messages WHERE turn_id=? AND speaker_id='ENTITY_AMORA' LIMIT 1",
        """SELECT 1 FROM amora_pg_v18_lab.speech_segments WHERE turn_id=%s
        UNION ALL SELECT 1 FROM amora_pg_v18_lab.conversation_messages
        WHERE turn_id=%s AND speaker_id='ENTITY_AMORA' LIMIT 1""",2,1)
    add("SELECT * FROM speech_segments WHERE turn_id = ? ORDER BY ordinal",
        "SELECT * FROM amora_pg_v18_lab.speech_segments WHERE turn_id=%s ORDER BY ordinal",1,10000)
    add("""UPDATE speech_segments SET status=?, interrupted=?,
        started_at=COALESCE(started_at, ?), finished_at=COALESCE(finished_at, ?)
        WHERE id=?""",
        """UPDATE amora_pg_v18_lab.speech_segments
        SET status=%s,interrupted=%s,started_at=COALESCE(started_at,%s),
            finished_at=COALESCE(finished_at,%s) WHERE id=%s""",5)
    add("""INSERT INTO model_usage(timestamp, provider, model, task_class, success, input_tokens, output_tokens,
        latency_ms, status_code, rate_limit_remaining_requests, rate_limit_remaining_tokens, turn_id, metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        """INSERT INTO amora_pg_v18_lab.model_usage(
            timestamp,provider,model,task_class,success,input_tokens,output_tokens,
            latency_ms,status_code,rate_limit_remaining_requests,
            rate_limit_remaining_tokens,turn_id,metadata_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",13)
    add("SELECT COUNT(*) FROM conversation_messages",
        "SELECT COUNT(*) FROM amora_pg_v18_lab.conversation_messages",0,1)
    add("SELECT COUNT(*) FROM cognitive_journal",
        "SELECT COUNT(*) FROM amora_pg_v18_lab.cognitive_journal",0,1)
    # V1 cognition observability must remain present when the PostgreSQL
    # candidate replaces the canonical V1 store. Keep a fixed SQL key for the
    # optional task_class filter rather than concatenating SQLite predicates.
    add(LATEST_USAGE, """
        SELECT * FROM amora_pg_v18_lab.model_usage
        WHERE provider=%s AND (%s::text IS NULL OR task_class=%s)
        ORDER BY timestamp DESC,id DESC LIMIT 1
    """,3,1)
    add("""SELECT provider, COUNT(*) AS request_count,
              SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count,
              COALESCE(SUM(input_tokens), 0) AS input_tokens,
              COALESCE(SUM(output_tokens), 0) AS output_tokens,
              MAX(timestamp) AS last_used_at
       FROM model_usage GROUP BY provider ORDER BY provider""",
        """SELECT provider, COUNT(*) AS request_count,
              SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) AS success_count,
              COALESCE(SUM(input_tokens),0) AS input_tokens,
              COALESCE(SUM(output_tokens),0) AS output_tokens,
              MAX(timestamp) AS last_used_at
        FROM amora_pg_v18_lab.model_usage
        GROUP BY provider ORDER BY provider""",0,1000)
    add(MERGE_METADATA, """
        UPDATE amora_pg_v18_lab.conversation_messages
        SET metadata_json=(COALESCE(metadata_json,'{}')::jsonb
                           || %s::jsonb)::text
        WHERE id=%s
    """,2)
    add("SELECT speech_text, metadata_json FROM speech_segments WHERE id = ?",
        """SELECT speech_text,metadata_json
        FROM amora_pg_v18_lab.speech_segments WHERE id=%s""",1,1)
    add("UPDATE speech_segments SET metadata_json = ? WHERE id = ?",
        """UPDATE amora_pg_v18_lab.speech_segments
        SET metadata_json=%s WHERE id=%s""",2)
    return m

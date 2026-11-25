"""Explicit PostgreSQL 18 reply-job statements, initially synthetic lab only.

The production domain registry does NOT import these candidate statements.
No runtime SQL rewriting. Identifiers are fixed; the caller must separately
install and certify the additive schema.
"""
from __future__ import annotations

from .postgres_runtime_connection import ReviewedStatement

# Avoid casting malformed/unbounded revision strings in historical metadata.
# The producer will author integer revisions; malformed rows fail eligibility.
_REVISION = """CASE
    WHEN jsonb_typeof(m.metadata_json::jsonb -> 'transport_revision')='number'
      AND (m.metadata_json::jsonb ->> 'transport_revision') ~ '^[1-9][0-9]{0,17}$'
    THEN (m.metadata_json::jsonb ->> 'transport_revision')::BIGINT
    ELSE NULL END"""
_ELIGIBLE = """
    m.dedupe_key >= 'cognitive-input:'
    AND m.dedupe_key < 'cognitive-input;'
    AND m.dedupe_key='cognitive-input:' || m.turn_id
    AND m.remote_processing_allowed=1 AND m.privacy_scope='PERSONAL'
    AND m.metadata_json::jsonb ->> 'surface'='platform_messaging'
    AND m.metadata_json::jsonb ->> 'platform'='discord'
    AND m.metadata_json::jsonb ->> 'channel_type'='dm'
    AND m.metadata_json::jsonb -> 'reply_enqueue_requested' IN ('true'::jsonb,'1'::jsonb)
    AND COALESCE(m.metadata_json::jsonb -> 'test_mode','false'::jsonb)
        IN ('false'::jsonb,'0'::jsonb,'null'::jsonb)
    AND m.session_id IS NOT NULL AND m.session_id<>''
    AND (m.metadata_json::jsonb ->> 'native_message_id_hash') ~ '^[0-9a-f]{20}$'
    AND (""" + _REVISION + """) >= 1
    AND length(m.metadata_json::jsonb ->> 'reply_outbound_id')=36
    AND NOT EXISTS (
        SELECT 1 FROM amora_pg_v18_lab.memory_reflection_invalidations x
        WHERE x.session_id=m.session_id
          AND x.native_message_hash=m.metadata_json::jsonb ->> 'native_message_id_hash'
          AND x.invalidated_through >= (""" + _REVISION + """)
    )
"""
_INSERT = """
    INSERT INTO amora_pg_v18_lab.messaging_reply_jobs(
        turn_id,input_id,session_id,native_message_hash,transport_revision,
        status,created_at,available_at)
    SELECT m.turn_id,m.id,m.session_id,
        m.metadata_json::jsonb ->> 'native_message_id_hash',
        """ + _REVISION + """, 'pending',m.started_at,%s
    FROM amora_pg_v18_lab.conversation_messages m
    WHERE
""" + _ELIGIBLE

_VALID_SOURCE = """
    NOT EXISTS (
        SELECT 1 FROM amora_pg_v18_lab.memory_reflection_invalidations x
        WHERE x.session_id=messaging_reply_jobs.session_id
          AND x.native_message_hash=messaging_reply_jobs.native_message_hash
          AND x.invalidated_through>=messaging_reply_jobs.transport_revision
    )
    AND EXISTS (
        SELECT 1 FROM amora_pg_v18_lab.conversation_messages m
        WHERE m.id=messaging_reply_jobs.input_id
          AND m.turn_id=messaging_reply_jobs.turn_id
          AND m.session_id=messaging_reply_jobs.session_id
          AND m.remote_processing_allowed=1
          AND m.privacy_scope='PERSONAL'
    )
"""
_OWNED = "turn_id=%s AND status='running' AND lease_token=%s AND lease_until>%s"


def reply_job_statements() -> dict[str, ReviewedStatement]:
    return {
        "PG_REPLY_JOB_CLAIM_LOCK": ReviewedStatement(
            "LOCK TABLE amora_pg_v18_lab.messaging_reply_jobs IN SHARE ROW EXCLUSIVE MODE", 0),
        "PG_REPLY_JOB_ENQUEUE": ReviewedStatement(
            _INSERT + " AND m.id=%s ON CONFLICT(turn_id) DO NOTHING", 2),
        "PG_REPLY_JOB_RECONCILE": ReviewedStatement(
            _INSERT + """
            AND NOT EXISTS (SELECT 1 FROM amora_pg_v18_lab.messaging_reply_jobs j
                WHERE j.turn_id=m.turn_id)
            ORDER BY m.started_at,m.turn_id LIMIT %s
            ON CONFLICT(turn_id) DO NOTHING
            """, 2),
        "PG_REPLY_JOB_CANCEL_INVALIDATED": ReviewedStatement("""
            UPDATE amora_pg_v18_lab.messaging_reply_jobs
            SET status='cancelled',lease_token=NULL,lease_until=NULL,
                last_reason='SOURCE_INVALIDATED'
            WHERE status IN ('pending','running','retry_wait','ready','blocked')
              AND turn_id IN (
                SELECT turn_id FROM amora_pg_v18_lab.messaging_reply_jobs
                WHERE status IN ('pending','running','retry_wait','ready','blocked')
                  AND NOT (""" + _VALID_SOURCE + """)
                ORDER BY created_at,job_seq LIMIT 128
              )""", 0),
        "PG_REPLY_JOB_RUNNING_COUNT": ReviewedStatement(
            "SELECT COUNT(*) FROM amora_pg_v18_lab.messaging_reply_jobs "
            "WHERE status='running' AND lease_until>%s", 1, 1),
        "PG_REPLY_JOB_SELECT_DUE": ReviewedStatement("""
            SELECT turn_id,input_id,session_id,attempts,native_message_hash,transport_revision
            FROM amora_pg_v18_lab.messaging_reply_jobs
            WHERE ((status IN ('pending','retry_wait') AND available_at<=%s)
                OR (status='running' AND lease_until<=%s))
              AND """ + _VALID_SOURCE + """
              AND NOT EXISTS (
                  SELECT 1 FROM amora_pg_v18_lab.messaging_inbound_generation g
                  WHERE g.turn_id=messaging_reply_jobs.turn_id AND (
                      g.session_id<>messaging_reply_jobs.session_id
                      OR g.native_message_hash<>messaging_reply_jobs.native_message_hash
                      OR g.transport_revision<>messaging_reply_jobs.transport_revision
                      OR (g.lease_token IS NOT NULL AND g.lease_until>%s)
                  )
              )
              AND NOT EXISTS (
                SELECT 1 FROM amora_pg_v18_lab.messaging_reply_jobs active
                WHERE active.session_id=messaging_reply_jobs.session_id
                  AND active.turn_id<>messaging_reply_jobs.turn_id
                  AND active.status='running' AND active.lease_until>%s
              )
              AND NOT EXISTS (
                SELECT 1 FROM amora_pg_v18_lab.messaging_reply_jobs earlier
                WHERE earlier.session_id=messaging_reply_jobs.session_id
                  AND earlier.status IN ('pending','retry_wait','running','ready')
                  AND (earlier.created_at<messaging_reply_jobs.created_at OR
                       (earlier.created_at=messaging_reply_jobs.created_at
                        AND earlier.job_seq<messaging_reply_jobs.job_seq))
              )
            ORDER BY available_at,created_at,job_seq LIMIT 1
            """, 4, 1),
        "PG_REPLY_JOB_CLAIM": ReviewedStatement("""
            UPDATE amora_pg_v18_lab.messaging_reply_jobs
            SET status='running',attempts=attempts+1,lease_token=%s,lease_until=%s
            WHERE turn_id=%s AND attempts=%s
              AND ((status IN ('pending','retry_wait') AND available_at<=%s)
                OR (status='running' AND lease_until<=%s))
              AND """ + _VALID_SOURCE, 6),
        "PG_REPLY_JOB_RENEW": ReviewedStatement(
            "UPDATE amora_pg_v18_lab.messaging_reply_jobs SET lease_until=%s WHERE "
            + _OWNED + " AND " + _VALID_SOURCE, 4),
        "PG_REPLY_JOB_DEFER": ReviewedStatement(
            "UPDATE amora_pg_v18_lab.messaging_reply_jobs SET status='retry_wait',"
            "available_at=%s,last_reason=%s,lease_token=NULL,lease_until=NULL WHERE "
            + _OWNED + " AND " + _VALID_SOURCE, 5),
        "PG_REPLY_JOB_BLOCK": ReviewedStatement(
            "UPDATE amora_pg_v18_lab.messaging_reply_jobs SET status='blocked',"
            "last_reason=%s,lease_token=NULL,lease_until=NULL WHERE " + _OWNED, 4),
        "PG_REPLY_JOB_RESUME": ReviewedStatement(
            "UPDATE amora_pg_v18_lab.messaging_reply_jobs SET status='pending',"
            "available_at=%s,last_reason=NULL WHERE turn_id=%s AND status='blocked' "
            "AND last_reason IN ('CONFIGURATION_BLOCKED','CONTEXT_BLOCKED') AND "
            + _VALID_SOURCE, 2),
        "PG_REPLY_JOB_MARK_READY": ReviewedStatement(
            "UPDATE amora_pg_v18_lab.messaging_reply_jobs SET status='ready',"
            "outbound_id=%s,lease_token=NULL,lease_until=NULL,last_reason=NULL WHERE "
            + _OWNED + " AND " + _VALID_SOURCE + """
            AND EXISTS (
                SELECT 1 FROM amora_pg_v18_lab.conversation_messages m
                JOIN amora_pg_v18_lab.messaging_outbound_queue o
                  ON o.id=m.metadata_json::jsonb ->> 'reply_outbound_id'
                WHERE m.id=messaging_reply_jobs.input_id AND o.id=%s
                  AND o.status IN ('queued','claimed','sent')
            )""", 5),
        "PG_REPLY_JOB_OBSERVE_SENT": ReviewedStatement("""
            UPDATE amora_pg_v18_lab.messaging_reply_jobs SET status='sent'
            WHERE turn_id=%s AND status='ready' AND EXISTS (
                SELECT 1 FROM amora_pg_v18_lab.messaging_outbound_queue o
                WHERE o.id=messaging_reply_jobs.outbound_id AND o.status='sent'
            )""", 1),
        "PG_REPLY_JOB_ENQUEUE_TURN": ReviewedStatement(_INSERT + " AND m.turn_id=%s ON CONFLICT(turn_id) DO NOTHING", 2),
        "PG_REPLY_JOB_LOAD": ReviewedStatement("""SELECT a.*,m.raw_text AS cognitive_text,m.metadata_json AS cognitive_metadata,
            m.speaker_id AS input_speaker,m.session_id AS input_session
            FROM amora_pg_v18_lab.messaging_reply_jobs
            JOIN amora_pg_v18_lab.conversation_messages m ON m.id=messaging_reply_jobs.input_id
            JOIN amora_pg_v18_lab.messaging_audit a ON a.id=m.metadata_json::jsonb ->> 'reply_audit_id'
            WHERE messaging_reply_jobs.turn_id=%s AND messaging_reply_jobs.status='running'
              AND messaging_reply_jobs.lease_token=%s AND messaging_reply_jobs.lease_until>%s
              AND a.direction='inbound' AND a.platform='discord' AND a.channel_type='dm'
              AND """ + _VALID_SOURCE, 3, 1),
        "PG_REPLY_JOB_FINALIZE_LOCK": ReviewedStatement("""SELECT messaging_reply_jobs.turn_id FROM amora_pg_v18_lab.messaging_reply_jobs
            JOIN amora_pg_v18_lab.messaging_inbound_generation g ON g.turn_id=messaging_reply_jobs.turn_id
            WHERE messaging_reply_jobs.turn_id=%s AND messaging_reply_jobs.status='running'
              AND messaging_reply_jobs.lease_token=%s AND messaging_reply_jobs.lease_until>%s
              AND g.lease_token=messaging_reply_jobs.lease_token
              AND g.lease_until=messaging_reply_jobs.lease_until
              AND """ + _VALID_SOURCE + " FOR UPDATE OF messaging_reply_jobs,g", 3, 1),
        "PG_REPLY_JOB_HISTORY_INSERT": ReviewedStatement("""INSERT INTO amora_pg_v18_lab.conversation_messages(
            id,turn_id,speaker_id,addressee_id,source,raw_text,normalized_text,spoken_text,
            privacy_scope,remote_processing_allowed,stt_confidence,provider,model,status,interrupted,
            source_event_id,session_id,started_at,finished_at,metadata_json,dedupe_key
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(dedupe_key) DO NOTHING""", 21),
        "PG_REPLY_JOB_SEGMENT_INSERT": ReviewedStatement("""INSERT INTO amora_pg_v18_lab.speech_segments(
            id,turn_id,ordinal,display_text,speech_text,status,tts_provider,voice_id,
            queued_at,started_at,finished_at,interrupted,metadata_json
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(turn_id,ordinal) DO NOTHING""", 13),
        "PG_REPLY_JOB_RECONCILE_OUTBOUND": ReviewedStatement("""UPDATE amora_pg_v18_lab.messaging_reply_jobs SET
            status=CASE WHEN EXISTS (SELECT 1 FROM amora_pg_v18_lab.messaging_outbound_queue o
                WHERE o.id=messaging_reply_jobs.outbound_id AND o.status='sent')
                THEN 'sent' ELSE 'blocked' END,
            last_reason=CASE WHEN EXISTS (SELECT 1 FROM amora_pg_v18_lab.messaging_outbound_queue o
                WHERE o.id=messaging_reply_jobs.outbound_id AND o.status='sent')
                THEN NULL ELSE 'DELIVERY_BLOCKED' END
            WHERE status='ready' AND turn_id IN (
                SELECT j.turn_id FROM amora_pg_v18_lab.messaging_reply_jobs j
                JOIN amora_pg_v18_lab.messaging_outbound_queue o ON o.id=j.outbound_id
                WHERE j.status='ready' AND o.status IN ('sent','failed','cancelled')
                ORDER BY j.created_at,j.job_seq LIMIT 128
            )""", 0),
        "PG_REPLY_JOB_COUNTS": ReviewedStatement("SELECT status,COUNT(*) AS count FROM amora_pg_v18_lab.messaging_reply_jobs GROUP BY status", 0, 7),
        "PG_REPLY_JOB_DELIVERY_ALLOWED": ReviewedStatement("""SELECT 1 FROM amora_pg_v18_lab.messaging_reply_jobs
            JOIN amora_pg_v18_lab.conversation_messages m ON m.id=messaging_reply_jobs.input_id
            JOIN amora_pg_v18_lab.messaging_audit a ON a.id=m.metadata_json::jsonb ->> 'reply_audit_id'
            WHERE messaging_reply_jobs.outbound_id=%s AND messaging_reply_jobs.status='ready'
              AND a.platform='discord' AND a.channel_type='dm' AND a.direction='inbound'
              AND a.account_scope=%s AND a.conversation_id=%s
              AND a.speaker_entity_id='PERSON_BK' AND m.speaker_id='PERSON_BK'
              AND COALESCE(a.metadata_json::jsonb -> 'durable_reply_transport' ->> 'native_message_id',a.message_id)=%s
              AND EXISTS (SELECT 1 FROM amora_pg_v18_lab.platform_identity_bindings b
                  WHERE b.platform='discord' AND b.account_scope=a.account_scope
                    AND b.external_actor_id=a.sender_external_id
                    AND b.identifier_type IN ('user_id','discord_user_id')
                    AND b.enabled=1 AND b.entity_id='PERSON_BK'
                    AND b.trust IN ('operator_verified','platform_authenticated'))
              AND NOT EXISTS (SELECT 1 FROM amora_pg_v18_lab.platform_identity_bindings b
                  WHERE b.platform='discord' AND b.account_scope=a.account_scope
                    AND b.external_actor_id=a.sender_external_id
                    AND b.identifier_type IN ('user_id','discord_user_id')
                    AND b.enabled=1 AND b.entity_id<>'PERSON_BK')
              AND """ + _VALID_SOURCE + " LIMIT 1", 4, 1),
        "PG_REPLY_JOB_JOURNAL_INSERT": ReviewedStatement("""INSERT INTO amora_pg_v18_lab.cognitive_journal(
            id,turn_id,stage,timestamp,source,summary,reason_codes_json,payload_json,
            privacy_scope,provider,model,duration_ms,correlation_id,error_code,metadata_json,dedupe_key
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(dedupe_key) DO NOTHING""", 16),
        "PG_REPLY_JOB_STATE": ReviewedStatement(
            "SELECT status,attempts,available_at,lease_until,last_reason,outbound_id "
            "FROM amora_pg_v18_lab.messaging_reply_jobs WHERE turn_id=%s", 1, 1),
    }

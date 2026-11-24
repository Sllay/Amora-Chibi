"""Fixed SQLite statements for the isolated reply-job candidate.

The PostgreSQL lab has separate explicitly reviewed statements keyed by these
operation names; no translation of caller-provided SQL at runtime.
"""
# Only Core-authored input metadata is eligible. A schema candidate cannot
# turn historical conversation rows into new remote work.
_ELIGIBLE = """
    m.dedupe_key >= 'cognitive-input:'
    AND m.dedupe_key < 'cognitive-input;'
    AND m.dedupe_key='cognitive-input:' || m.turn_id
    AND m.remote_processing_allowed=1 AND m.privacy_scope='PERSONAL'
    AND json_extract(m.metadata_json,'$.surface')='platform_messaging'
    AND json_extract(m.metadata_json,'$.platform')='discord'
    AND json_extract(m.metadata_json,'$.channel_type')='dm'
    AND json_extract(m.metadata_json,'$.reply_enqueue_requested')=1
    AND COALESCE(json_extract(m.metadata_json,'$.test_mode'),0)=0
    AND m.session_id IS NOT NULL AND m.session_id<>''
    AND length(json_extract(m.metadata_json,'$.native_message_id_hash'))=20
    AND json_extract(m.metadata_json,'$.native_message_id_hash') NOT GLOB '*[^0-9a-f]*'
    AND typeof(json_extract(m.metadata_json,'$.transport_revision'))='integer'
    AND json_extract(m.metadata_json,'$.transport_revision')>=1
    AND length(json_extract(m.metadata_json,'$.reply_outbound_id'))=36
    AND NOT EXISTS (
        SELECT 1 FROM memory_reflection_invalidations x
        WHERE x.session_id=m.session_id
          AND x.native_message_hash=json_extract(m.metadata_json,'$.native_message_id_hash')
          AND x.invalidated_through>=json_extract(m.metadata_json,'$.transport_revision')
    )
"""
_INSERT = """
    INSERT INTO messaging_reply_jobs(
        turn_id,input_id,session_id,native_message_hash,transport_revision,
        status,created_at,available_at)
    SELECT m.turn_id,m.id,m.session_id,
        json_extract(m.metadata_json,'$.native_message_id_hash'),
        json_extract(m.metadata_json,'$.transport_revision'),
        'pending',m.started_at,?
    FROM conversation_messages m
    WHERE
""" + _ELIGIBLE

_VALID_SOURCE = """
    NOT EXISTS (
        SELECT 1 FROM memory_reflection_invalidations x
        WHERE x.session_id=messaging_reply_jobs.session_id
          AND x.native_message_hash=messaging_reply_jobs.native_message_hash
          AND x.invalidated_through>=messaging_reply_jobs.transport_revision
    )
    AND EXISTS (
        SELECT 1 FROM conversation_messages m
        WHERE m.id=messaging_reply_jobs.input_id
          AND m.turn_id=messaging_reply_jobs.turn_id
          AND m.session_id=messaging_reply_jobs.session_id
          AND m.remote_processing_allowed=1
          AND m.privacy_scope='PERSONAL'
    )
"""
_OWNED = "turn_id=? AND status='running' AND lease_token=? AND lease_until>?"


SQLITE_STATEMENTS = {
    "ENQUEUE": (_INSERT + " AND m.id=? ON CONFLICT(turn_id) DO NOTHING"),
    "RECONCILE": (_INSERT + """
            AND NOT EXISTS (SELECT 1 FROM messaging_reply_jobs j WHERE j.turn_id=m.turn_id)
            ORDER BY m.started_at,m.turn_id LIMIT ?
            ON CONFLICT(turn_id) DO NOTHING
            """),
    "CANCEL_INVALIDATED": ("""
            UPDATE messaging_reply_jobs
            SET status='cancelled',lease_token=NULL,lease_until=NULL,
                last_reason='SOURCE_INVALIDATED'
            WHERE turn_id IN (
                SELECT turn_id FROM messaging_reply_jobs
                WHERE status IN ('pending','running','retry_wait','ready','blocked')
                  AND NOT (""" + _VALID_SOURCE + """)
                ORDER BY created_at,turn_id LIMIT 128
            )"""),
    "RUNNING_COUNT": ("SELECT COUNT(*) FROM messaging_reply_jobs "
                "WHERE status='running' AND lease_until>?"),
    "SELECT_DUE": ("""
                SELECT turn_id,input_id,session_id,attempts,native_message_hash,transport_revision
                FROM messaging_reply_jobs
                WHERE ((status IN ('pending','retry_wait') AND available_at<=?)
                    OR (status='running' AND lease_until<=?))
                  AND """ + _VALID_SOURCE + """
                  AND NOT EXISTS (
                      SELECT 1 FROM messaging_inbound_generation g
                      WHERE g.turn_id=messaging_reply_jobs.turn_id AND (
                          g.session_id<>messaging_reply_jobs.session_id
                          OR g.native_message_hash<>messaging_reply_jobs.native_message_hash
                          OR g.transport_revision<>messaging_reply_jobs.transport_revision
                          OR (g.lease_token IS NOT NULL AND g.lease_until>?)
                      )
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM messaging_reply_jobs active
                    WHERE active.session_id=messaging_reply_jobs.session_id
                      AND active.turn_id<>messaging_reply_jobs.turn_id
                      AND active.status='running' AND active.lease_until>?
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM messaging_reply_jobs earlier
                    WHERE earlier.session_id=messaging_reply_jobs.session_id
                      AND earlier.status IN ('pending','retry_wait','running','ready')
                      AND (earlier.created_at<messaging_reply_jobs.created_at OR
                          (earlier.created_at=messaging_reply_jobs.created_at
                           AND earlier.turn_id<messaging_reply_jobs.turn_id))
                  )
                ORDER BY available_at,created_at,turn_id LIMIT 1
            """),
    "CLAIM": ("""
                UPDATE messaging_reply_jobs SET status='running',attempts=attempts+1,
                    lease_token=?,lease_until=?
                WHERE turn_id=? AND attempts=?
                    AND ((status IN ('pending','retry_wait') AND available_at<=?)
                        OR (status='running' AND lease_until<=?))
                    AND """ + _VALID_SOURCE),
    "RENEW": ("UPDATE messaging_reply_jobs SET lease_until=? WHERE " + _OWNED
            + " AND " + _VALID_SOURCE),
    "DEFER": ("UPDATE messaging_reply_jobs SET status='retry_wait',available_at=?,"
            "last_reason=?,lease_token=NULL,lease_until=NULL WHERE " + _OWNED
            + " AND " + _VALID_SOURCE),
    "BLOCK": ("UPDATE messaging_reply_jobs SET status='blocked',last_reason=?,"
            "lease_token=NULL,lease_until=NULL WHERE " + _OWNED),
    "RESUME": ("UPDATE messaging_reply_jobs SET status='pending',available_at=?,"
            "last_reason=NULL WHERE turn_id=? AND status='blocked' "
            "AND last_reason IN ('CONFIGURATION_BLOCKED','CONTEXT_BLOCKED') AND "
            + _VALID_SOURCE),
    "MARK_READY": ("UPDATE messaging_reply_jobs SET status='ready',outbound_id=?,"
            "lease_token=NULL,lease_until=NULL,last_reason=NULL WHERE " + _OWNED
            + " AND " + _VALID_SOURCE + """
            AND EXISTS (
                SELECT 1 FROM conversation_messages m
                JOIN messaging_outbound_queue o
                  ON o.id=json_extract(m.metadata_json,'$.reply_outbound_id')
                WHERE m.id=messaging_reply_jobs.input_id AND o.id=?
                  AND o.status IN ('queued','claimed','sent')
            )"""),
    "OBSERVE_SENT": ("""
            UPDATE messaging_reply_jobs SET status='sent'
            WHERE turn_id=? AND status='ready' AND EXISTS (
                SELECT 1 FROM messaging_outbound_queue o
                WHERE o.id=messaging_reply_jobs.outbound_id AND o.status='sent'
            )"""),
    "ENQUEUE_TURN": (_INSERT + " AND m.turn_id=? ON CONFLICT(turn_id) DO NOTHING"),
    "LOAD": ("""SELECT a.*,m.raw_text AS cognitive_text,m.metadata_json AS cognitive_metadata,
            m.speaker_id AS input_speaker,m.session_id AS input_session
            FROM messaging_reply_jobs
            JOIN conversation_messages m ON m.id=messaging_reply_jobs.input_id
            JOIN messaging_audit a ON a.id=json_extract(m.metadata_json,'$.reply_audit_id')
            WHERE messaging_reply_jobs.turn_id=? AND messaging_reply_jobs.status='running'
              AND messaging_reply_jobs.lease_token=? AND messaging_reply_jobs.lease_until>?
              AND a.direction='inbound' AND a.platform='discord' AND a.channel_type='dm'
              AND """ + _VALID_SOURCE),
    "FINALIZE_LOCK": ("""SELECT messaging_reply_jobs.turn_id FROM messaging_reply_jobs
            JOIN messaging_inbound_generation g ON g.turn_id=messaging_reply_jobs.turn_id
            WHERE messaging_reply_jobs.turn_id=? AND messaging_reply_jobs.status='running'
              AND messaging_reply_jobs.lease_token=? AND messaging_reply_jobs.lease_until>?
              AND g.lease_token=messaging_reply_jobs.lease_token
              AND g.lease_until=messaging_reply_jobs.lease_until
              AND """ + _VALID_SOURCE),
    "HISTORY_INSERT": ("""INSERT INTO conversation_messages(
            id,turn_id,speaker_id,addressee_id,source,raw_text,normalized_text,spoken_text,
            privacy_scope,remote_processing_allowed,stt_confidence,provider,model,status,interrupted,
            source_event_id,session_id,started_at,finished_at,metadata_json,dedupe_key
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(dedupe_key) DO NOTHING"""),
    "SEGMENT_INSERT": ("""INSERT INTO speech_segments(
            id,turn_id,ordinal,display_text,speech_text,status,tts_provider,voice_id,
            queued_at,started_at,finished_at,interrupted,metadata_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(turn_id,ordinal) DO NOTHING"""),
    "RECONCILE_OUTBOUND": ("""UPDATE messaging_reply_jobs SET
            status=CASE WHEN EXISTS (SELECT 1 FROM messaging_outbound_queue o
                WHERE o.id=messaging_reply_jobs.outbound_id AND o.status='sent')
                THEN 'sent' ELSE 'blocked' END,
            last_reason=CASE WHEN EXISTS (SELECT 1 FROM messaging_outbound_queue o
                WHERE o.id=messaging_reply_jobs.outbound_id AND o.status='sent')
                THEN NULL ELSE 'DELIVERY_BLOCKED' END
            WHERE status='ready' AND turn_id IN (
                SELECT j.turn_id FROM messaging_reply_jobs j
                JOIN messaging_outbound_queue o ON o.id=j.outbound_id
                WHERE j.status='ready' AND o.status IN ('sent','failed','cancelled')
                ORDER BY j.created_at,j.turn_id LIMIT 128
            )"""),
    "COUNTS": ("SELECT status,COUNT(*) AS count FROM messaging_reply_jobs GROUP BY status"),
    "STATE": ("SELECT status,attempts,available_at,lease_until,last_reason,outbound_id "
                "FROM messaging_reply_jobs WHERE turn_id=?"),
}

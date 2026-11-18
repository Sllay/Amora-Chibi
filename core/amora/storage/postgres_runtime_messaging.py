"""Reviewed PostgreSQL messaging audit, outbound queue and presence SQL.

This is an opt-in synthetic Core contract. No production connector/Discord
transport is switched. Unknown agenda/initiative SQL stays fail closed.
"""
from __future__ import annotations
from .postgres_runtime_connection import ReviewedStatement

AUDIT_LIST="PG_MESSAGING_AUDIT_LIST"
OUTBOUND_INSERT="PG_MESSAGING_OUTBOUND_INSERT"
OUTBOUND_EXISTS="PG_MESSAGING_OUTBOUND_EXISTS"


def messaging_statements()->dict[str,ReviewedStatement]:
    m:dict[str,ReviewedStatement]={}
    def add(src,pg,n,cap=1000):
        key=" ".join(src.split())
        if key in m: raise ValueError("PG_MESSAGING_DUPLICATE_SQL")
        m[key]=ReviewedStatement(pg,n,cap)

    add("""INSERT INTO messaging_audit(
        id, platform, account_scope, conversation_id, message_id,
        direction, channel_type, sender_external_id, speaker_entity_id,
        text, reply_to_message_id, reply_to_author_entity_id,
        mentions_json, routing_action, reason_codes_json, responded,
        timestamp, metadata_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        """INSERT INTO amora_pg_v18_lab.messaging_audit(
        id,platform,account_scope,conversation_id,message_id,
        direction,channel_type,sender_external_id,speaker_entity_id,
        text,reply_to_message_id,reply_to_author_entity_id,
        mentions_json,routing_action,reason_codes_json,responded,
        timestamp,metadata_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT DO NOTHING""",18)
    add("""SELECT * FROM messaging_audit
        WHERE platform=? AND account_scope=? AND conversation_id=?
          AND message_id=? AND direction=? LIMIT 1""",
        """SELECT * FROM amora_pg_v18_lab.messaging_audit
        WHERE platform=%s AND account_scope=%s AND conversation_id=%s
        AND message_id=%s AND direction=%s LIMIT 1""",5,1)
    add("""UPDATE messaging_audit SET responded=1
        WHERE platform=? AND account_scope=? AND conversation_id=?
          AND message_id=? AND direction='inbound'""",
        """UPDATE amora_pg_v18_lab.messaging_audit SET responded=1
        WHERE platform=%s AND account_scope=%s AND conversation_id=%s
        AND message_id=%s AND direction='inbound'""",4)
    add("""SELECT reason_codes_json FROM messaging_audit
        WHERE platform=? AND account_scope=? AND conversation_id=?
          AND message_id=? AND direction='inbound'""",
        """SELECT reason_codes_json FROM amora_pg_v18_lab.messaging_audit
        WHERE platform=%s AND account_scope=%s AND conversation_id=%s
        AND message_id=%s AND direction='inbound' FOR UPDATE""",4,1)
    add("""UPDATE messaging_audit SET reason_codes_json=?
        WHERE platform=? AND account_scope=? AND conversation_id=?
          AND message_id=? AND direction='inbound'""",
        """UPDATE amora_pg_v18_lab.messaging_audit SET reason_codes_json=%s
        WHERE platform=%s AND account_scope=%s AND conversation_id=%s
        AND message_id=%s AND direction='inbound'""",5)
    add("SELECT COUNT(*) FROM messaging_audit",
        "SELECT COUNT(*) FROM amora_pg_v18_lab.messaging_audit",0,1)
    add(AUDIT_LIST,"""SELECT * FROM amora_pg_v18_lab.messaging_audit
        WHERE (%s::text IS NULL OR platform=%s)
          AND (%s::text IS NULL OR conversation_id=%s)
        ORDER BY timestamp DESC LIMIT %s""",5,500)
    add(OUTBOUND_INSERT,"""
        INSERT INTO amora_pg_v18_lab.messaging_outbound_queue(
            id,platform,account_scope,conversation_id,target_entity_id,text,
            reply_to_message_id,mention_actor_ids_json,source_message_id,
            created_at,status,metadata_json
        )
        SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
        WHERE (
            %s::text IS NULL OR EXISTS (
                SELECT 1 FROM amora_pg_v18_lab.messaging_inbound_generation g
                WHERE g.turn_id=%s AND g.lease_token=%s AND g.lease_until>%s
                  AND NOT EXISTS(
                    SELECT 1 FROM amora_pg_v18_lab.memory_reflection_invalidations x
                    WHERE x.session_id=g.session_id
                      AND x.native_message_hash=g.native_message_hash
                      AND x.invalidated_through>=g.transport_revision
                  )
            )
        )
        AND (
            %s=0 OR EXISTS(
                SELECT 1 FROM amora_pg_v18_lab.memory_records m
                WHERE m.id=%s AND m.owner_entity_id=%s
                  AND m.privacy_scope='PERSONAL' AND m.archived_at IS NULL
                  AND m.updated_at=%s
            )
        )
        AND (
            %s=0 OR EXISTS (
                SELECT 1 FROM amora_pg_v18_lab.messaging_inbound_generation g
                WHERE g.turn_id=%s AND g.session_id=%s
            )
        )
        ON CONFLICT(id) DO NOTHING
    """,23)
    add(OUTBOUND_EXISTS,
        "SELECT 1 FROM amora_pg_v18_lab.messaging_outbound_queue WHERE id=%s",1,1)
    add("SELECT * FROM messaging_outbound_queue WHERE id=?",
        "SELECT * FROM amora_pg_v18_lab.messaging_outbound_queue WHERE id=%s",1,1)
    add("""SELECT 1 FROM messaging_outbound_queue
        WHERE platform=? AND account_scope=? AND status='queued'
        UNION ALL
        SELECT 1 FROM messaging_outbound_queue
        WHERE platform=? AND account_scope=? AND status='claimed'
          AND claim_expires_at < ?
        LIMIT 1""",
        """SELECT 1 FROM amora_pg_v18_lab.messaging_outbound_queue
        WHERE platform=%s AND account_scope=%s AND status='queued'
        UNION ALL SELECT 1 FROM amora_pg_v18_lab.messaging_outbound_queue
        WHERE platform=%s AND account_scope=%s AND status='claimed'
        AND claim_expires_at<%s LIMIT 1""",5,1)
    add("""SELECT * FROM messaging_outbound_queue
        WHERE platform=? AND account_scope=? AND (
            status='queued' OR (status='claimed' AND claim_expires_at < ?)
        ) ORDER BY created_at LIMIT ?""",
        """SELECT * FROM amora_pg_v18_lab.messaging_outbound_queue
        WHERE platform=%s AND account_scope=%s
        AND (status='queued' OR (status='claimed' AND claim_expires_at<%s))
        ORDER BY created_at LIMIT %s FOR UPDATE SKIP LOCKED""",4,32)
    add(
        "UPDATE messaging_outbound_queue SET status='failed',error_code='AGENDA_DELIVERY_UNCERTAIN',claim_expires_at=NULL WHERE id=? AND status='claimed'",
        "UPDATE amora_pg_v18_lab.messaging_outbound_queue SET status='failed',error_code='AGENDA_DELIVERY_UNCERTAIN',claim_expires_at=NULL WHERE id=%s AND status='claimed'",
        1,
    )
    add(
        "UPDATE messaging_outbound_queue SET status='cancelled',error_code='AGENDA_STALE_OR_UNAUTHORIZED',claim_expires_at=NULL WHERE id=? AND status='queued'",
        "UPDATE amora_pg_v18_lab.messaging_outbound_queue SET status='cancelled',error_code='AGENDA_STALE_OR_UNAUTHORIZED',claim_expires_at=NULL WHERE id=%s AND status='queued'",
        1,
    )
    add(
        "UPDATE messaging_outbound_queue SET status='cancelled',error_code='TEMPORAL_INITIATIVE_STALE',claim_expires_at=NULL WHERE id=? AND status IN ('queued','claimed')",
        "UPDATE amora_pg_v18_lab.messaging_outbound_queue SET status='cancelled',error_code='TEMPORAL_INITIATIVE_STALE',claim_expires_at=NULL WHERE id=%s AND status IN ('queued','claimed')",
        1,
    )
    add(
        "UPDATE messaging_outbound_queue SET status='cancelled',error_code='MEMORY_DISCLOSURE_STALE',claim_expires_at=NULL WHERE id=? AND status IN ('queued','claimed')",
        "UPDATE amora_pg_v18_lab.messaging_outbound_queue SET status='cancelled',error_code='MEMORY_DISCLOSURE_STALE',claim_expires_at=NULL WHERE id=%s AND status IN ('queued','claimed')",
        1,
    )
    add("""UPDATE messaging_outbound_queue
        SET status='claimed', claimed_at=?, claim_expires_at=?
        WHERE id=? AND (status='queued' OR (status='claimed' AND claim_expires_at < ?))""",
        """UPDATE amora_pg_v18_lab.messaging_outbound_queue
        SET status='claimed',claimed_at=%s,claim_expires_at=%s
        WHERE id=%s AND (status='queued' OR
            (status='claimed' AND claim_expires_at<%s))""",4)
    add("""UPDATE messaging_outbound_queue
        SET status=?, external_message_id=?, error_code=?, claim_expires_at=NULL,
            metadata_json=CASE WHEN ?='sent' THEN json_set(metadata_json,'$.delivery_confirmed_at',?) ELSE metadata_json END
        WHERE id=? AND status IN ('queued','claimed')""",
        """UPDATE amora_pg_v18_lab.messaging_outbound_queue
        SET status=%s,external_message_id=%s,error_code=%s,claim_expires_at=NULL,
        metadata_json=CASE WHEN %s='sent' THEN
            jsonb_set(metadata_json::jsonb,'{delivery_confirmed_at}',
                to_jsonb(%s::text),true)::text ELSE metadata_json END
        WHERE id=%s AND status IN ('queued','claimed')""",6)
    add("""UPDATE messaging_outbound_queue
        SET status='cancelled', error_code=?, claim_expires_at=NULL
        WHERE id=? AND status IN ('queued','claimed')""",
        """UPDATE amora_pg_v18_lab.messaging_outbound_queue
        SET status='cancelled',error_code=%s,claim_expires_at=NULL
        WHERE id=%s AND status IN ('queued','claimed')""",2)
    add("""UPDATE messaging_outbound_queue
        SET status='cancelled', error_code=?, claim_expires_at=NULL
        WHERE platform=? AND account_scope=? AND conversation_id=?
          AND source_message_id=? AND status IN ('queued','claimed')
          AND COALESCE(json_extract(metadata_json,'$.transport_revision'),1)<=?""",
        """UPDATE amora_pg_v18_lab.messaging_outbound_queue
        SET status='cancelled',error_code=%s,claim_expires_at=NULL
        WHERE platform=%s AND account_scope=%s AND conversation_id=%s
          AND source_message_id=%s AND status IN ('queued','claimed')
          AND COALESCE((metadata_json::jsonb ->> 'transport_revision')::BIGINT,1)<=%s""",6)
    add("SELECT COUNT(*) FROM messaging_outbound_queue WHERE status IN ('queued','claimed')",
        """SELECT COUNT(*) FROM amora_pg_v18_lab.messaging_outbound_queue
        WHERE status IN ('queued','claimed')""",0,1)
    add("SELECT * FROM platform_presence WHERE platform=? AND account_scope=?",
        """SELECT * FROM amora_pg_v18_lab.platform_presence
        WHERE platform=%s AND account_scope=%s""",2,1)
    add("""INSERT INTO platform_presence(
        platform, account_scope, state, desired_connected, check_requested,
        generation, updated_at, last_connected_at, last_disconnected_at,
        last_error_code
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(platform, account_scope) DO UPDATE SET
        state=excluded.state,
        desired_connected=excluded.desired_connected,
        check_requested=excluded.check_requested,
        generation=excluded.generation,
        updated_at=excluded.updated_at,
        last_connected_at=excluded.last_connected_at,
        last_disconnected_at=excluded.last_disconnected_at,
        last_error_code=excluded.last_error_code""",
        """INSERT INTO amora_pg_v18_lab.platform_presence(
        platform,account_scope,state,desired_connected,check_requested,
        generation,updated_at,last_connected_at,last_disconnected_at,last_error_code)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(platform,account_scope) DO UPDATE SET
        state=EXCLUDED.state,desired_connected=EXCLUDED.desired_connected,
        check_requested=EXCLUDED.check_requested,generation=EXCLUDED.generation,
        updated_at=EXCLUDED.updated_at,last_connected_at=EXCLUDED.last_connected_at,
        last_disconnected_at=EXCLUDED.last_disconnected_at,
        last_error_code=EXCLUDED.last_error_code""",10)
    add("SELECT * FROM platform_presence ORDER BY platform, account_scope",
        "SELECT * FROM amora_pg_v18_lab.platform_presence ORDER BY platform,account_scope",0,1000)
    return m

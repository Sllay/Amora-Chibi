"""Explicit PostgreSQL SQL for owner-only agenda event ledger and DM checks.

Authorization still requires canonical _valid_target() and agenda_claim_allowed()
over authenticated owner, exact Discord DM, and current presence. These SQL
mappings do not authorize an audience.
"""
from __future__ import annotations
from .postgres_runtime_connection import ReviewedStatement

def agenda_statements()->dict[str,ReviewedStatement]:
    m:dict[str,ReviewedStatement]={}
    def add(src,pg,n,cap=1000):
        key=" ".join(src.split())
        if key in m: raise ValueError("PG_AGENDA_DUPLICATE_QUERY")
        m[key]=ReviewedStatement(pg,n,cap)
    add("""SELECT entity_id,enabled,trust FROM platform_identity_bindings
        WHERE platform='discord' AND account_scope=? AND external_actor_id=?
        AND identifier_type IN ('user_id','discord_user_id')""",
        """SELECT entity_id,enabled,trust FROM amora_pg_v18_lab.platform_identity_bindings
        WHERE platform='discord' AND account_scope=%s AND external_actor_id=%s
        AND identifier_type IN ('user_id','discord_user_id')""",2,1000)
    add("""SELECT 1 FROM messaging_audit WHERE platform='discord' AND account_scope=?
        AND conversation_id=? AND channel_type='dm' AND direction='inbound'
        AND speaker_entity_id=? AND sender_external_id=? LIMIT 1""",
        """SELECT 1 FROM amora_pg_v18_lab.messaging_audit
        WHERE platform='discord' AND account_scope=%s AND conversation_id=%s
        AND channel_type='dm' AND direction='inbound'
        AND speaker_entity_id=%s AND sender_external_id=%s LIMIT 1""",4,1)
    add("""SELECT 1 FROM platform_presence WHERE platform='discord' AND account_scope=?
        AND state='connected' AND desired_connected=1 LIMIT 1""",
        """SELECT 1 FROM amora_pg_v18_lab.platform_presence
        WHERE platform='discord' AND account_scope=%s
        AND state='connected' AND desired_connected=1 LIMIT 1""",1,1)
    add("""SELECT account_scope,conversation_id,sender_external_id FROM messaging_audit
        WHERE platform='discord' AND channel_type='dm' AND direction='inbound'
        AND speaker_entity_id=?
        ORDER BY julianday(timestamp) DESC,id DESC LIMIT 30""",
        """SELECT account_scope,conversation_id,sender_external_id
        FROM amora_pg_v18_lab.messaging_audit
        WHERE platform='discord' AND channel_type='dm' AND direction='inbound'
          AND speaker_entity_id=%s
        ORDER BY timestamp::timestamptz DESC,id DESC LIMIT 30""",1,30)
    add("""SELECT id,payload_json FROM events WHERE actor=? AND event_type=?
        ORDER BY timestamp DESC LIMIT ?""",
        """SELECT id,payload_json FROM amora_pg_v18_lab.events
        WHERE actor=%s AND event_type=%s
        ORDER BY timestamp DESC LIMIT %s""",3,2501)
    add("""SELECT payload_json FROM events WHERE actor=? AND event_type=?
        ORDER BY timestamp DESC LIMIT ?""",
        """SELECT payload_json FROM amora_pg_v18_lab.events
        WHERE actor=%s AND event_type=%s
        ORDER BY timestamp DESC LIMIT %s""",3,2501)
    add("""UPDATE messaging_outbound_queue SET status='cancelled',error_code='AGENDA_CANCELLED',
        claim_expires_at=NULL WHERE status IN ('queued','claimed')
        AND json_extract(metadata_json,'$.agenda_id')=?""",
        """UPDATE amora_pg_v18_lab.messaging_outbound_queue
        SET status='cancelled',error_code='AGENDA_CANCELLED',claim_expires_at=NULL
        WHERE status IN ('queued','claimed')
          AND metadata_json::jsonb ->> 'agenda_id'=%s""",1)
    add("""SELECT id FROM messaging_outbound_queue WHERE status='queued'
        AND json_extract(metadata_json,'$.agenda_id')=?
        AND json_extract(metadata_json,'$.agenda_expires_at') <= ?""",
        """SELECT id FROM amora_pg_v18_lab.messaging_outbound_queue
        WHERE status='queued' AND metadata_json::jsonb ->> 'agenda_id'=%s
          AND metadata_json::jsonb ->> 'agenda_expires_at'<=%s
        ORDER BY created_at,id LIMIT 200""",2,200)
    add("UPDATE messaging_outbound_queue SET status='cancelled',error_code='AGENDA_EXPIRED' WHERE id=? AND status='queued'",
        """UPDATE amora_pg_v18_lab.messaging_outbound_queue
        SET status='cancelled',error_code='AGENDA_EXPIRED'
        WHERE id=%s AND status='queued'""",1)
    add("""SELECT 1 FROM messaging_outbound_queue WHERE status IN ('queued','claimed')
        AND json_extract(metadata_json,'$.agenda_id')=? LIMIT 1""",
        """SELECT 1 FROM amora_pg_v18_lab.messaging_outbound_queue
        WHERE status IN ('queued','claimed')
          AND metadata_json::jsonb ->> 'agenda_id'=%s LIMIT 1""",1,1)
    add("SELECT status,error_code FROM messaging_outbound_queue WHERE id=?",
        """SELECT status,error_code FROM amora_pg_v18_lab.messaging_outbound_queue
        WHERE id=%s""",1,1)
    return m

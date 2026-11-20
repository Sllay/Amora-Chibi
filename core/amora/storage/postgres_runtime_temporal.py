"""Reviewed PostgreSQL 18 owner-DM temporal initiative policy queries.

No autonomous contact is authorized by this registry. Existing application
guards revalidate the latest verified real Discord DM and a sent response.
Timestamp comparisons explicitly use PostgreSQL timestamptz casts.
"""
from __future__ import annotations
from .postgres_runtime_connection import ReviewedStatement

def temporal_statements()->dict[str,ReviewedStatement]:
    m:dict[str,ReviewedStatement]={}
    def add(src,pg,n,cap=1000):
        key=" ".join(src.split())
        if key in m:raise ValueError("PG_TEMPORAL_DUPLICATE_SQL")
        m[key]=ReviewedStatement(pg,n,cap)

    add("""SELECT * FROM messaging_audit WHERE platform='discord' AND channel_type='dm'
        AND direction='inbound' AND speaker_entity_id=?
        ORDER BY julianday(timestamp) DESC,id DESC LIMIT 1""",
        """SELECT * FROM amora_pg_v18_lab.messaging_audit
        WHERE platform='discord' AND channel_type='dm'
          AND direction='inbound' AND speaker_entity_id=%s
        ORDER BY timestamp::timestamptz DESC,id DESC LIMIT 1""",1,1)
    add("""SELECT id FROM messaging_audit WHERE platform='discord' AND account_scope=?
        AND conversation_id=? AND direction='inbound'
        ORDER BY julianday(timestamp) DESC,id DESC LIMIT 1""",
        """SELECT id FROM amora_pg_v18_lab.messaging_audit
        WHERE platform='discord' AND account_scope=%s AND conversation_id=%s
          AND direction='inbound'
        ORDER BY timestamp::timestamptz DESC,id DESC LIMIT 1""",2,1)
    # The platform binding query is shared verbatim with agenda_statements.
    add("""SELECT 1 FROM platform_presence WHERE platform='discord' AND account_scope=?
        AND state='connected' AND desired_connected=1""",
        """SELECT 1 FROM amora_pg_v18_lab.platform_presence
        WHERE platform='discord' AND account_scope=%s
          AND state='connected' AND desired_connected=1""",1,1)
    add("""SELECT * FROM conversation_messages WHERE dedupe_key=? AND turn_id=?
        AND session_id=? AND speaker_id=? AND addressee_id='ENTITY_AMORA'
        AND status='received' AND privacy_scope='PERSONAL' AND remote_processing_allowed=1""",
        """SELECT * FROM amora_pg_v18_lab.conversation_messages
        WHERE dedupe_key=%s AND turn_id=%s AND session_id=%s AND speaker_id=%s
          AND addressee_id='ENTITY_AMORA' AND status='received'
          AND privacy_scope='PERSONAL' AND remote_processing_allowed=1""",4,1)
    add("""SELECT 1 FROM conversation_messages WHERE speaker_id=? AND status='received'
        AND julianday(started_at)>julianday(?) AND COALESCE(dedupe_key,'')<>?
        AND COALESCE(json_extract(metadata_json,'$.test_mode'),0)=0
        AND COALESCE(json_extract(metadata_json,'$.cognitive_mode'),'reply')<>'autonomous_initiation'
        LIMIT 1""",
        """SELECT 1 FROM amora_pg_v18_lab.conversation_messages
        WHERE speaker_id=%s AND status='received'
          AND started_at::timestamptz>%s::timestamptz
          AND COALESCE(dedupe_key,'')<>%s
          AND COALESCE(metadata_json::jsonb ->> 'test_mode','0') IN ('0','false')
          AND COALESCE(metadata_json::jsonb ->> 'cognitive_mode','reply')<>'autonomous_initiation'
        LIMIT 1""",3,1)
    add("""SELECT * FROM messaging_outbound_queue WHERE id=? AND platform='discord'
        AND account_scope=? AND conversation_id=? AND target_entity_id=? AND status='sent'""",
        """SELECT * FROM amora_pg_v18_lab.messaging_outbound_queue
        WHERE id=%s AND platform='discord' AND account_scope=%s
          AND conversation_id=%s AND target_entity_id=%s AND status='sent'""",4,1)
    add("""SELECT 1 FROM messaging_outbound_queue WHERE platform='discord' AND account_scope=?
        AND conversation_id=? AND status IN ('queued','claimed') AND id<>? LIMIT 1""",
        """SELECT 1 FROM amora_pg_v18_lab.messaging_outbound_queue
        WHERE platform='discord' AND account_scope=%s AND conversation_id=%s
          AND status IN ('queued','claimed') AND id<>%s LIMIT 1""",3,1)
    add("SELECT 1 FROM events WHERE event_type=? AND target=? AND timestamp>=? LIMIT 1",
        """SELECT 1 FROM amora_pg_v18_lab.events
        WHERE event_type=%s AND target=%s AND timestamp>=%s LIMIT 1""",3,1)
    add("SELECT 1 FROM events WHERE dedupe_key=?",
        "SELECT 1 FROM amora_pg_v18_lab.events WHERE dedupe_key=%s",1,1)
    return m

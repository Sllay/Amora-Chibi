"""Reviewed PostgreSQL for canonical ConversationFloor and runtime turn lookup.

The Discord Core may pass through ConversationFloor while Runtime v2 is
enabled. Every source key is a fixed canonical SQL literal and every target
uses the disposable PG18 namespace. No SQL translation occurs at runtime.
"""
from __future__ import annotations

from .postgres_runtime_connection import ReviewedStatement


def conversation_floor_statements() -> dict[str, ReviewedStatement]:
    m: dict[str, ReviewedStatement]={}

    def add(source:str,pg:str,params:int,cap:int=1000)->None:
        key=" ".join(source.split())
        if key in m:
            raise ValueError("PG_CONVERSATION_FLOOR_DUPLICATE_SQL")
        m[key]=ReviewedStatement(pg,params,cap)

    add(
        """SELECT * FROM messaging_conversation_floor
           WHERE platform=? AND account_scope=? AND conversation_id=?""",
        """SELECT * FROM amora_pg_v18_lab.messaging_conversation_floor
           WHERE platform=%s AND account_scope=%s AND conversation_id=%s""",3,1,
    )
    add(
        """SELECT turn_id FROM conversation_runtime_turns
           WHERE session_id=? AND source_message_id=?""",
        """SELECT turn_id FROM amora_pg_v18_lab.conversation_runtime_turns
           WHERE session_id=%s AND source_message_id=%s""",2,1,
    )
    add(
        """UPDATE messaging_conversation_floor
           SET speaker_actor_ref=NULL, speaker_entity_id=NULL, opened_at=NULL,
               expires_at=NULL, anchor_message_id=NULL,
               response_source_message_id=NULL, reason=NULL
           WHERE platform=? AND account_scope=? AND conversation_id=?""",
        """UPDATE amora_pg_v18_lab.messaging_conversation_floor
           SET speaker_actor_ref=NULL,speaker_entity_id=NULL,opened_at=NULL,
               expires_at=NULL,anchor_message_id=NULL,
               response_source_message_id=NULL,reason=NULL
           WHERE platform=%s AND account_scope=%s AND conversation_id=%s""",3,
    )
    add(
        """INSERT INTO messaging_conversation_floor(
               platform, account_scope, conversation_id, generation,
               speaker_actor_ref, speaker_entity_id, opened_at, expires_at,
               anchor_message_id, response_source_message_id, reason
           ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(platform, account_scope, conversation_id) DO UPDATE SET
               generation=messaging_conversation_floor.generation + 1,
               speaker_actor_ref=excluded.speaker_actor_ref,
               speaker_entity_id=excluded.speaker_entity_id,
               opened_at=excluded.opened_at,
               expires_at=excluded.expires_at,
               anchor_message_id=excluded.anchor_message_id,
               response_source_message_id=excluded.response_source_message_id,
               reason=excluded.reason""",
        """INSERT INTO amora_pg_v18_lab.messaging_conversation_floor(
               platform,account_scope,conversation_id,generation,
               speaker_actor_ref,speaker_entity_id,opened_at,expires_at,
               anchor_message_id,response_source_message_id,reason
           ) VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT(platform,account_scope,conversation_id) DO UPDATE SET
               generation=amora_pg_v18_lab.messaging_conversation_floor.generation+1,
               speaker_actor_ref=EXCLUDED.speaker_actor_ref,
               speaker_entity_id=EXCLUDED.speaker_entity_id,
               opened_at=EXCLUDED.opened_at,
               expires_at=EXCLUDED.expires_at,
               anchor_message_id=EXCLUDED.anchor_message_id,
               response_source_message_id=EXCLUDED.response_source_message_id,
               reason=EXCLUDED.reason""",10,
    )
    add(
        """UPDATE messaging_conversation_floor
           SET generation=generation + 1, anchor_message_id=?,
               response_source_message_id=?, reason=?
           WHERE platform=? AND account_scope=? AND conversation_id=?
             AND generation=? AND speaker_actor_ref=? AND expires_at>=?""",
        """UPDATE amora_pg_v18_lab.messaging_conversation_floor
           SET generation=generation+1,anchor_message_id=%s,
               response_source_message_id=%s,reason=%s
           WHERE platform=%s AND account_scope=%s AND conversation_id=%s
             AND generation=%s AND speaker_actor_ref=%s AND expires_at>=%s""",9,
    )
    add(
        """INSERT INTO messaging_conversation_floor(
               platform, account_scope, conversation_id, generation
           ) VALUES (?, ?, ?, 1)
           ON CONFLICT(platform, account_scope, conversation_id) DO UPDATE SET
               generation=messaging_conversation_floor.generation + 1,
               speaker_actor_ref=NULL, speaker_entity_id=NULL, opened_at=NULL,
               expires_at=NULL, anchor_message_id=NULL,
               response_source_message_id=NULL, reason=NULL""",
        """INSERT INTO amora_pg_v18_lab.messaging_conversation_floor(
               platform,account_scope,conversation_id,generation
           ) VALUES (%s,%s,%s,1)
           ON CONFLICT(platform,account_scope,conversation_id) DO UPDATE SET
               generation=amora_pg_v18_lab.messaging_conversation_floor.generation+1,
               speaker_actor_ref=NULL,speaker_entity_id=NULL,opened_at=NULL,
               expires_at=NULL,anchor_message_id=NULL,
               response_source_message_id=NULL,reason=NULL""",3,
    )
    return m

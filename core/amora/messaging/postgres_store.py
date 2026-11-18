"""PostgreSQL candidate MessagingStore with transactional disclosure fencing.

The original MessagingStore remains the transport/domain authority. Dynamic
SQLite SQL for outbound generation leases, memory disclosure and audit
listings is replaced by explicit reviewed PostgreSQL statements.
No production activation or fallbacks.
"""
from __future__ import annotations

import json

from amora.latency_trace import trace_lock
from amora.memory.disclosure import SHAREABLE_MEMORY_SQL
from amora.storage.postgres_runtime_connection import PostgresRuntimeError
from amora.storage.postgres_runtime_memory import SHAREABLE_OWNER
from amora.storage.postgres_runtime_messaging import (
    AUDIT_LIST, OUTBOUND_INSERT, OUTBOUND_EXISTS,
)
from amora.messaging.generation_lifecycle import InboundGenerationLost
from amora.messaging.session_identity import messaging_session_ref

from .explicit_memory import OWNER_ENTITY_ID
from .models import MessagingAuditRecord, MessagingPlatform, OutboundMessage
from .store import MessagingStore


class PostgresMessagingStore(MessagingStore):
    def __init__(self,database,clock):
        if database.backend.capabilities.name!="postgres-candidate":
            raise PostgresRuntimeError("PG_MESSAGING_BACKEND_REQUIRED")
        super().__init__(database,clock)

    def list_audit(self,*,platform:MessagingPlatform|None=None,
                   conversation_id:str|None=None,limit:int=100)->tuple[MessagingAuditRecord,...]:
        platform_value=platform.value if platform is not None else None
        with trace_lock(self.database._lock):
            rows=self.database._conn.execute(
                AUDIT_LIST,(platform_value,platform_value,
                            conversation_id,conversation_id,
                            max(1,min(500,int(limit)))),
            ).fetchall()
        return tuple(self._audit_from_row(row) for row in rows)

    def disclosure_is_current(self,message:OutboundMessage)->bool:
        """Mirror the original owner-only single-turn grant and wording gates."""
        from .disclosure import public_text_allowed,audience_text_allowed

        grant=message.metadata.get("memory_disclosure")
        if not isinstance(grant,dict):
            return False
        now=self.clock.now().isoformat()
        session_id=messaging_session_ref(
            message.platform.value,message.account_scope,message.conversation_id,
        )
        if (grant.get("session_id")!=session_id
                or grant.get("turn_id")!=message.metadata.get("cognitive_turn_id")
                or grant.get("owner_id")!=OWNER_ENTITY_ID
                or message.target_entity_id!=OWNER_ENTITY_ID
                or not grant.get("record_id")
                or not grant.get("updated_at")
                or not grant.get("expires_at")
                or not isinstance(grant["expires_at"],str)
                or grant["expires_at"]<=now):
            return False
        source_excerpt=grant.get("source_excerpt",message.text)
        if (not isinstance(source_excerpt,str)
                or not public_text_allowed(source_excerpt)
                or not audience_text_allowed(source_excerpt,audience_id=session_id)
                or not public_text_allowed(message.text)
                or not audience_text_allowed(message.text,audience_id=session_id)):
            return False
        with trace_lock(self.database._lock):
            row=self.database._conn.execute(
                SHAREABLE_OWNER,(
                    OWNER_ENTITY_ID,now,now,str(grant["record_id"]),
                ),
            ).fetchone()
        return row is not None and row["updated_at"]==grant["updated_at"]

    def put_outbound(self,message:OutboundMessage,*,generation_lease=None)->bool:
        """Single guarded SQL write, without dynamic SQL concatenation.

        The caller's transaction locks the source grant row for the duration
        of the outbound insert. It refuses stale source/lease and prevents
        duplicate queue entries.
        """
        grant=message.metadata.get("memory_disclosure")
        if grant is not None:
            if (generation_lease is None or not isinstance(grant,dict)
                    or grant.get("turn_id")!=generation_lease[0]
                    or grant.get("owner_id")!=OWNER_ENTITY_ID
                    or message.target_entity_id!=OWNER_ENTITY_ID):
                raise InboundGenerationLost("MEMORY_DISCLOSURE_GRANT_INVALID")
        now=self.clock.now().isoformat()
        values=(
            str(message.id),message.platform.value,message.account_scope,
            message.conversation_id,message.target_entity_id,message.text,
            message.reply_to_message_id,
            json.dumps(message.mention_actor_ids,separators=(",",":")),
            message.source_message_id,message.created_at.isoformat(),
            message.status.value,
            json.dumps(message.metadata,ensure_ascii=False,separators=(",",":")),
        )
        if generation_lease is None:
            lease_values=(None,None,None,None)
        else:
            turn,token=generation_lease
            lease_values=(str(turn),str(turn),str(token),now)
        if grant is None:
            grant_values=(0,None,None,None)
            session_values=(0,None,None)
        else:
            grant_values=(
                1,str(grant.get("record_id")),OWNER_ENTITY_ID,
                str(grant.get("updated_at")),
            )
            session_values=(
                1,str(generation_lease[0]),str(grant.get("session_id")),
            )
        with trace_lock(self.database._lock),self.database._conn:
            if grant is not None and not self.disclosure_is_current(message):
                raise InboundGenerationLost("MEMORY_DISCLOSURE_GRANT_INVALID")
            cursor=self.database._conn.execute(
                OUTBOUND_INSERT,
                (*values,*lease_values,*grant_values,*session_values),
            )
            if cursor.rowcount==1:
                return True
            if self.database._conn.execute(
                OUTBOUND_EXISTS,(str(message.id),),
            ).fetchone() is not None:
                return False
            if generation_lease is not None:
                raise InboundGenerationLost("INBOUND_GENERATION_LEASE_LOST")
            raise PostgresRuntimeError("PG_MESSAGING_OUTBOUND_INSERT_NO_ROW")

from __future__ import annotations

import json
from datetime import datetime, timedelta
from uuid import UUID

from amora.storage.backend import execute_atomic_statement
from amora.time import Clock

from .models import (
    AuditDirection,
    ChannelType,
    MessagingAuditRecord,
    MessagingPlatform,
    OutboundMessage,
    OutboundStatus,
    PresenceSnapshot,
    PresenceState,
    RoutingAction,
)


class MessagingStore:
    """Persistence facade over the canonical Amora database.

    Messaging history is intentionally separate from Memory. This store may keep
    private platform identifiers for operator audit, but callers must use explicit
    private/admin surfaces to reveal them. Health/state projections expose counts
    only.
    """

    def __init__(self, database, clock: Clock) -> None:
        self.database = database
        self.clock = clock

    def append_audit(self, record: MessagingAuditRecord) -> bool:
        values = (
            str(record.id),
            record.platform.value,
            record.account_scope,
            record.conversation_id,
            record.message_id,
            record.direction.value,
            record.channel_type.value,
            record.sender_external_id,
            record.speaker_entity_id,
            record.text,
            record.reply_to_message_id,
            record.reply_to_author_entity_id,
            json.dumps(record.mentions, ensure_ascii=False, separators=(",", ":")),
            record.routing_action.value if record.routing_action else None,
            json.dumps(record.reason_codes, ensure_ascii=False, separators=(",", ":")),
            int(record.responded),
            record.timestamp.isoformat(),
            json.dumps(record.metadata, ensure_ascii=False, separators=(",", ":")),
        )
        with self.database._lock:
            try:
                execute_atomic_statement(
                    self.database.backend,
                    self.database._conn,
                    """INSERT INTO messaging_audit(
                           id, platform, account_scope, conversation_id, message_id,
                           direction, channel_type, sender_external_id, speaker_entity_id,
                           text, reply_to_message_id, reply_to_author_entity_id,
                           mentions_json, routing_action, reason_codes_json, responded,
                           timestamp, metadata_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    values,
                )
                return True
            except Exception as exc:
                if "UNIQUE" not in str(exc).upper() and "DUPLICATE" not in str(exc).upper():
                    raise
                return False

    def has_audit(
        self,
        *,
        platform: MessagingPlatform | str,
        account_scope: str,
        conversation_id: str,
        message_id: str,
        direction: AuditDirection = AuditDirection.INBOUND,
    ) -> bool:
        platform_value = platform.value if isinstance(platform, MessagingPlatform) else str(platform)
        with self.database._lock:
            row = self.database._conn.execute(
                """SELECT 1 FROM messaging_audit
                   WHERE platform=? AND account_scope=? AND conversation_id=?
                     AND message_id=? AND direction=? LIMIT 1""",
                (platform_value, account_scope, conversation_id, message_id, direction.value),
            ).fetchone()
        return row is not None

    def mark_inbound_responded(
        self,
        *,
        platform: MessagingPlatform,
        account_scope: str,
        conversation_id: str,
        message_id: str,
    ) -> None:
        with self.database._lock:
            execute_atomic_statement(
                self.database.backend,
                self.database._conn,
                """UPDATE messaging_audit SET responded=1
                   WHERE platform=? AND account_scope=? AND conversation_id=?
                     AND message_id=? AND direction='inbound'""",
                (platform.value, account_scope, conversation_id, message_id),
            )

    def add_inbound_reason(
        self,
        *,
        platform: MessagingPlatform,
        account_scope: str,
        conversation_id: str,
        message_id: str,
        reason: str,
    ) -> None:
        with self.database._lock, self.database._conn:
            row = self.database._conn.execute(
                """SELECT reason_codes_json FROM messaging_audit
                   WHERE platform=? AND account_scope=? AND conversation_id=?
                     AND message_id=? AND direction='inbound'""",
                (platform.value, account_scope, conversation_id, message_id),
            ).fetchone()
            if row is None:
                return
            reasons = list(json.loads(row["reason_codes_json"] or "[]"))
            if reason not in reasons:
                reasons.append(reason[:120])
            self.database._conn.execute(
                """UPDATE messaging_audit SET reason_codes_json=?
                   WHERE platform=? AND account_scope=? AND conversation_id=?
                     AND message_id=? AND direction='inbound'""",
                (
                    json.dumps(reasons, ensure_ascii=False, separators=(",", ":")),
                    platform.value,
                    account_scope,
                    conversation_id,
                    message_id,
                ),
            )

    def count_audit(self) -> int:
        with self.database._lock:
            row = self.database._conn.execute("SELECT COUNT(*) FROM messaging_audit").fetchone()
        return int(row[0]) if row else 0

    def list_audit(
        self,
        *,
        platform: MessagingPlatform | None = None,
        conversation_id: str | None = None,
        limit: int = 100,
    ) -> tuple[MessagingAuditRecord, ...]:
        limit = max(1, min(500, int(limit)))
        clauses: list[str] = []
        params: list[object] = []
        if platform is not None:
            clauses.append("platform=?")
            params.append(platform.value)
        if conversation_id is not None:
            clauses.append("conversation_id=?")
            params.append(conversation_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.database._lock:
            rows = self.database._conn.execute(
                f"SELECT * FROM messaging_audit{where} ORDER BY timestamp DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return tuple(self._audit_from_row(row) for row in rows)

    def put_outbound(self, message: OutboundMessage) -> bool:
        with self.database._lock:
            try:
                execute_atomic_statement(
                    self.database.backend,
                    self.database._conn,
                    """INSERT INTO messaging_outbound_queue(
                           id, platform, account_scope, conversation_id, target_entity_id,
                           text, reply_to_message_id, mention_actor_ids_json, source_message_id,
                           created_at, status, metadata_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(message.id),
                        message.platform.value,
                        message.account_scope,
                        message.conversation_id,
                        message.target_entity_id,
                        message.text,
                        message.reply_to_message_id,
                        json.dumps(message.mention_actor_ids, separators=(",", ":")),
                        message.source_message_id,
                        message.created_at.isoformat(),
                        message.status.value,
                        json.dumps(message.metadata, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
                return True
            except Exception as exc:
                if "UNIQUE" not in str(exc).upper() and "DUPLICATE" not in str(exc).upper():
                    raise
                return False

    def claim_outbound(
        self,
        *,
        platform: MessagingPlatform,
        account_scope: str,
        limit: int = 8,
        lease_seconds: int = 30,
    ) -> tuple[OutboundMessage, ...]:
        limit = max(1, min(32, int(limit)))
        now = self.clock.now()
        expires = now + timedelta(seconds=max(5, min(120, int(lease_seconds))))
        claimed: list[OutboundMessage] = []
        with self.database._lock, self.database._conn:
            rows = self.database._conn.execute(
                """SELECT * FROM messaging_outbound_queue
                   WHERE platform=? AND account_scope=? AND (
                       status='queued' OR (status='claimed' AND claim_expires_at < ?)
                   ) ORDER BY created_at LIMIT ?""",
                (platform.value, account_scope, now.isoformat(), limit),
            ).fetchall()
            for row in rows:
                cursor = self.database._conn.execute(
                    """UPDATE messaging_outbound_queue
                       SET status='claimed', claimed_at=?, claim_expires_at=?
                       WHERE id=? AND (status='queued' OR (status='claimed' AND claim_expires_at < ?))""",
                    (now.isoformat(), expires.isoformat(), row["id"], now.isoformat()),
                )
                if getattr(cursor, "rowcount", 1) == 0:
                    continue
                claimed.append(
                    self._outbound_from_row(
                        dict(row) | {
                            "status": OutboundStatus.CLAIMED.value,
                            "claimed_at": now.isoformat(),
                            "claim_expires_at": expires.isoformat(),
                        }
                    )
                )
        return tuple(claimed)

    def ack_outbound(
        self,
        message_id: UUID | str,
        *,
        sent: bool,
        external_message_id: str | None = None,
        error_code: str | None = None,
    ) -> bool:
        status = OutboundStatus.SENT if sent else OutboundStatus.FAILED
        with self.database._lock:
            cursor = execute_atomic_statement(
                self.database.backend,
                self.database._conn,
                """UPDATE messaging_outbound_queue
                   SET status=?, external_message_id=?, error_code=?, claim_expires_at=NULL
                   WHERE id=? AND status IN ('queued','claimed')""",
                (status.value, external_message_id, error_code, str(message_id)),
            )
        return getattr(cursor, "rowcount", 1) > 0

    def cancel_outbound(self, message_id: UUID | str, *, reason: str) -> bool:
        with self.database._lock:
            cursor = execute_atomic_statement(
                self.database.backend,
                self.database._conn,
                """UPDATE messaging_outbound_queue
                   SET status='cancelled', error_code=?, claim_expires_at=NULL
                   WHERE id=? AND status IN ('queued','claimed')""",
                (reason[:120], str(message_id)),
            )
        return getattr(cursor, "rowcount", 1) > 0

    def cancel_outbound_by_source(
        self,
        *,
        platform: MessagingPlatform,
        account_scope: str,
        conversation_id: str,
        source_message_id: str,
        reason: str,
    ) -> int:
        """Cancel queued/claimed replies anchored to a native platform message."""
        with self.database._lock:
            cursor = execute_atomic_statement(
                self.database.backend,
                self.database._conn,
                """UPDATE messaging_outbound_queue
                   SET status='cancelled', error_code=?, claim_expires_at=NULL
                   WHERE platform=? AND account_scope=? AND conversation_id=?
                     AND source_message_id=? AND status IN ('queued','claimed')""",
                (
                    reason[:120],
                    platform.value,
                    account_scope,
                    conversation_id,
                    source_message_id,
                ),
            )
        return max(0, int(getattr(cursor, "rowcount", 0) or 0))

    def pending_outbound_count(self) -> int:
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT COUNT(*) FROM messaging_outbound_queue WHERE status IN ('queued','claimed')"
            ).fetchone()
        return int(row[0]) if row else 0

    def get_presence(
        self,
        platform: MessagingPlatform,
        account_scope: str = "primary",
    ) -> PresenceSnapshot | None:
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM platform_presence WHERE platform=? AND account_scope=?",
                (platform.value, account_scope),
            ).fetchone()
        return self._presence_from_row(row) if row else None

    def put_presence(self, snapshot: PresenceSnapshot) -> PresenceSnapshot:
        with self.database._lock:
            execute_atomic_statement(
                self.database.backend,
                self.database._conn,
                """INSERT INTO platform_presence(
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
                (
                    snapshot.platform.value,
                    snapshot.account_scope,
                    snapshot.state.value,
                    int(snapshot.desired_connected),
                    int(snapshot.check_requested),
                    snapshot.generation,
                    snapshot.updated_at.isoformat(),
                    snapshot.last_connected_at.isoformat() if snapshot.last_connected_at else None,
                    snapshot.last_disconnected_at.isoformat() if snapshot.last_disconnected_at else None,
                    snapshot.last_error_code,
                ),
            )
        return snapshot

    def list_presence(self) -> tuple[PresenceSnapshot, ...]:
        with self.database._lock:
            rows = self.database._conn.execute(
                "SELECT * FROM platform_presence ORDER BY platform, account_scope"
            ).fetchall()
        return tuple(self._presence_from_row(row) for row in rows)

    @staticmethod
    def _audit_from_row(row) -> MessagingAuditRecord:
        return MessagingAuditRecord(
            id=UUID(str(row["id"])),
            platform=MessagingPlatform(row["platform"]),
            account_scope=row["account_scope"],
            conversation_id=row["conversation_id"],
            message_id=row["message_id"],
            direction=AuditDirection(row["direction"]),
            channel_type=ChannelType(row["channel_type"]),
            sender_external_id=row["sender_external_id"],
            speaker_entity_id=row["speaker_entity_id"],
            text=row["text"],
            reply_to_message_id=row["reply_to_message_id"],
            reply_to_author_entity_id=row["reply_to_author_entity_id"],
            mentions=list(json.loads(row["mentions_json"] or "[]")),
            routing_action=RoutingAction(row["routing_action"]) if row["routing_action"] else None,
            reason_codes=list(json.loads(row["reason_codes_json"] or "[]")),
            responded=bool(row["responded"]),
            timestamp=datetime.fromisoformat(row["timestamp"]),
            metadata=dict(json.loads(row["metadata_json"] or "{}")),
        )

    @staticmethod
    def _outbound_from_row(row) -> OutboundMessage:
        return OutboundMessage(
            id=UUID(str(row["id"])),
            platform=MessagingPlatform(row["platform"]),
            account_scope=row["account_scope"],
            conversation_id=row["conversation_id"],
            target_entity_id=row["target_entity_id"],
            text=row["text"],
            reply_to_message_id=row["reply_to_message_id"],
            mention_actor_ids=list(json.loads(row["mention_actor_ids_json"] or "[]")),
            source_message_id=row["source_message_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            status=OutboundStatus(row["status"]),
            metadata=dict(json.loads(row["metadata_json"] or "{}")),
        )

    @staticmethod
    def _presence_from_row(row) -> PresenceSnapshot:
        return PresenceSnapshot(
            platform=MessagingPlatform(row["platform"]),
            account_scope=row["account_scope"],
            state=PresenceState(row["state"]),
            desired_connected=bool(row["desired_connected"]),
            check_requested=bool(row["check_requested"]),
            generation=int(row["generation"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            last_connected_at=(
                datetime.fromisoformat(row["last_connected_at"])
                if row["last_connected_at"] else None
            ),
            last_disconnected_at=(
                datetime.fromisoformat(row["last_disconnected_at"])
                if row["last_disconnected_at"] else None
            ),
            last_error_code=row["last_error_code"],
        )

"""Durable owner agenda on the existing event ledger and outbound queue.

No new DB schema, cache as source of truth, background process or model-prompt
promise. Every due occurrence has one deterministic outbound id.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from uuid import UUID, NAMESPACE_URL, uuid5

from amora.events import AmoraEvent, PrivacyScope
from amora.latency_trace import trace_lock
from amora.messaging.models import MessagingPlatform, OutboundMessage
from .models import AgendaCreate, AgendaItem, OWNER, event_payload, next_occurrence, occurrence, utc

CREATE_TYPE = "agenda.created"
CANCEL_TYPE = "agenda.cancelled"
MAX_ITEMS = 2500
MAX_ACTIVE = 64


def _valid_target(conn, account: str, conversation: str, external_id: str, *, connected: bool = False):
    if not all(isinstance(v, str) and v for v in (account, conversation, external_id)):
        return False
    if not (external_id.isdecimal() and 15 <= len(external_id) <= 22):
        return False
    matches = conn.execute(
        """SELECT entity_id,enabled,trust FROM platform_identity_bindings
           WHERE platform='discord' AND account_scope=? AND external_actor_id=?
           AND identifier_type IN ('user_id','discord_user_id')""",
        (account, external_id),
    ).fetchall()
    enabled = [row for row in matches if row["enabled"] == 1]
    if (not enabled or any(row["entity_id"] != OWNER for row in enabled)
            or not any(row["trust"] in {"operator_verified", "platform_authenticated"} for row in enabled)):
        return False
    known = conn.execute(
        """SELECT 1 FROM messaging_audit WHERE platform='discord' AND account_scope=?
           AND conversation_id=? AND channel_type='dm' AND direction='inbound'
           AND speaker_entity_id=? AND sender_external_id=? LIMIT 1""",
        (account, conversation, OWNER, external_id),
    ).fetchone()
    if known is None:
        return False
    if connected:
        row = conn.execute(
            """SELECT 1 FROM platform_presence WHERE platform='discord' AND account_scope=?
               AND state='connected' AND desired_connected=1 LIMIT 1""",
            (account,),
        ).fetchone()
        if row is None:
            return False
    return True


def _default_target(conn):
    candidates = conn.execute(
        """SELECT account_scope,conversation_id,sender_external_id FROM messaging_audit
           WHERE platform='discord' AND channel_type='dm' AND direction='inbound'
           AND speaker_entity_id=?
           ORDER BY julianday(timestamp) DESC,id DESC LIMIT 30""", (OWNER,),
    ).fetchall()
    for row in candidates:
        account, conversation, external = row
        if _valid_target(conn, account, conversation, external):
            return {"account_scope": account, "conversation_id": conversation,
                    "external_actor_id": external}
    raise ValueError("AGENDA_VERIFIED_DM_REQUIRED")


def _ledger(conn):
    rows = conn.execute(
        """SELECT id,payload_json FROM events WHERE actor=? AND event_type=?
           ORDER BY timestamp DESC LIMIT ?""", (OWNER, CREATE_TYPE, MAX_ITEMS + 1),
    ).fetchall()
    if len(rows) > MAX_ITEMS:
        raise ValueError("AGENDA_HISTORY_LIMIT")
    cancellations = conn.execute(
        """SELECT payload_json FROM events WHERE actor=? AND event_type=?
           ORDER BY timestamp DESC LIMIT ?""", (OWNER, CANCEL_TYPE, MAX_ITEMS + 1),
    ).fetchall()
    if len(cancellations) > MAX_ITEMS:
        raise ValueError("AGENDA_HISTORY_LIMIT")
    cancelled = set()
    for row in cancellations:
        try:
            cancelled.add(str(UUID(json.loads(row["payload_json"])["agenda_id"])))
        except (ValueError, TypeError, KeyError):
            raise ValueError("AGENDA_CORRUPT_CANCEL") from None
    items = []
    for row in rows:
        try:
            value = json.loads(row["payload_json"])
            item = AgendaItem.model_validate(value)
            if str(item.id) != row["id"]:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise ValueError("AGENDA_CORRUPT_ITEM") from None
        items.append(item.model_copy(update={"status": "cancelled" if str(item.id) in cancelled else "active"}))
    return items


class AgendaService:
    def __init__(self, database, clock, messaging_store):
        self.db = database
        self.clock = clock
        self.messaging_store = messaging_store

    @staticmethod
    def _same_request(previous, request):
        return (previous.request_id == request.request_id
                and previous.text == request.text.strip()
                and utc(previous.due_at) == utc(request.due_at)
                and previous.timezone == request.timezone
                and previous.interval_minutes == request.interval_minutes
                and (utc(previous.until_at) if previous.until_at else None)
                    == (utc(request.until_at) if request.until_at else None))

    def list(self, *, year: int | None = None, include_cancelled: bool = True):
        with trace_lock(self.db._lock):
            items = _ledger(self.db._conn)
        if year is not None:
            items = [i for i in items if i.due_at.astimezone(ZoneInfo(i.timezone)).year == year]
        if not include_cancelled:
            items = [i for i in items if i.status == "active"]
        return sorted(items, key=lambda x: (x.due_at, str(x.id)))

    def create(self, request: AgendaCreate, *, target: dict | None = None):
        now = utc(self.clock.now())
        due = utc(request.due_at)
        if due <= now + timedelta(seconds=10) or due > now + timedelta(days=365 * 30):
            raise ValueError("AGENDA_DATE_OUT_OF_RANGE")
        end = utc(request.until_at) if request.until_at else None
        if end is not None and end <= now:
            raise ValueError("AGENDA_END_IN_PAST")
        event_id = uuid5(NAMESPACE_URL, f"amora:agenda:create:{request.request_id}")
        with trace_lock(self.db._lock):
            items = _ledger(self.db._conn)
            previous = next((x for x in items if x.id == event_id), None)
            if previous is not None:
                # Don't let a replay with a changed body claim successful creation.
                if not self._same_request(previous, request):
                    raise ValueError("AGENDA_REQUEST_CONFLICT")
                return previous
            if sum(i.status == "active" for i in items) >= MAX_ACTIVE:
                raise ValueError("AGENDA_ACTIVE_LIMIT")
            if target is None:
                target = _default_target(self.db._conn)
            if not _valid_target(self.db._conn, target["account_scope"],
                                 target["conversation_id"], target["external_actor_id"]):
                raise ValueError("AGENDA_TARGET_NOT_VERIFIED")
        item = AgendaItem(id=event_id, request_id=request.request_id, text=request.text,
                          due_at=due, timezone=request.timezone,
                          interval_minutes=request.interval_minutes, until_at=end,
                          created_at=now, **target)
        event = AmoraEvent(
            id=event_id, event_type=CREATE_TYPE, timestamp=now, source="agenda.owner_request",
            actor=OWNER, target=OWNER, privacy_scope=PrivacyScope.PERSONAL,
            dedupe_key=f"agenda:create:{request.request_id}", payload=event_payload(item),
        )
        if not self.db.append_event(event):
            with trace_lock(self.db._lock):
                found = next((x for x in _ledger(self.db._conn) if x.id == event_id), None)
            if found is None or not self._same_request(found, request):
                raise ValueError("AGENDA_REQUEST_CONFLICT")
            return found
        return item

    def cancel(self, agenda_id: UUID):
        with trace_lock(self.db._lock):
            item = next((x for x in _ledger(self.db._conn) if x.id == agenda_id), None)
        if item is None:
            raise KeyError("AGENDA_NOT_FOUND")
        if item.status == "cancelled":
            return item
        event = AmoraEvent(
            id=uuid5(NAMESPACE_URL, f"amora:agenda:cancel:{agenda_id}"),
            event_type=CANCEL_TYPE, timestamp=utc(self.clock.now()),
            source="agenda.owner_cancel", actor=OWNER, target=OWNER,
            privacy_scope=PrivacyScope.PERSONAL,
            dedupe_key=f"agenda:cancel:{agenda_id}", payload={"agenda_id": str(agenda_id)},
        )
        self.db.append_event(event)
        # A persisted cancel record is the source of truth. A claimed outbound
        # could be in-flight; don't claim that an already sent message is revoked.
        with trace_lock(self.db._lock), self.db._conn:
            self.db._conn.execute(
                """UPDATE messaging_outbound_queue SET status='cancelled',error_code='AGENDA_CANCELLED',
                   claim_expires_at=NULL WHERE status IN ('queued','claimed')
                   AND json_extract(metadata_json,'$.agenda_id')=?""", (str(agenda_id),),
            )
        return item.model_copy(update={"status": "cancelled"})

    def tick(self):
        """Scan bounded active agenda, queue only the due current occurrence."""
        now = utc(self.clock.now())
        with trace_lock(self.db._lock):
            items = _ledger(self.db._conn)
        queued = []
        for item in items:
            if item.status != "active":
                continue
            when = occurrence(item, now)
            if when is None:
                continue
            key = f"amora:agenda:occurrence:{item.id}:{when.isoformat()}"
            oid = uuid5(NAMESPACE_URL, key)
            msg = OutboundMessage(
                id=oid, platform=MessagingPlatform.DISCORD,
                account_scope=item.account_scope, conversation_id=item.conversation_id,
                target_entity_id=OWNER, text=item.text,
                created_at=now,
                metadata={"agenda_id": str(item.id), "agenda_occurrence": when.isoformat(),
                          "agenda_expires_at": (when + timedelta(minutes=5)).isoformat(),
                          "agenda_external_actor_id": item.external_actor_id,
                          "agenda_source": "explicit_owner_schedule",
                          "agenda_state": "recurring_prompt" if item.interval_minutes else "scheduled_reminder"},
            )
            with trace_lock(self.db._lock):
                # Do not accumulate dozens of expired queued reminders while
                # a connected Discord worker is not polling.
                old = self.db._conn.execute(
                    """SELECT id FROM messaging_outbound_queue WHERE status='queued'
                       AND json_extract(metadata_json,'$.agenda_id')=?
                       AND json_extract(metadata_json,'$.agenda_expires_at') <= ?""",
                    (str(item.id), now.isoformat()),
                ).fetchall()
                if old:
                    with self.db._conn:
                        for entry in old[:200]:
                            self.db._conn.execute(
                                "UPDATE messaging_outbound_queue SET status='cancelled',error_code='AGENDA_EXPIRED' WHERE id=? AND status='queued'",
                                (entry["id"],),
                            )
                pending = self.db._conn.execute(
                    """SELECT 1 FROM messaging_outbound_queue WHERE status IN ('queued','claimed')
                       AND json_extract(metadata_json,'$.agenda_id')=? LIMIT 1""",
                    (str(item.id),),
                ).fetchone()
                if pending or not agenda_claim_allowed(self.db._conn, msg, now, require_connected=True):
                    continue
                if self.messaging_store.put_outbound(msg):
                    queued.append(msg)
        return queued

    def projection(self, *, year: int | None = None):
        now = utc(self.clock.now())
        out = []
        for item in self.list(year=year):
            due = next_occurrence(item, now) if item.status == "active" else None
            status = ("cancelled" if item.status == "cancelled" else
                      "scheduled" if due is not None else "finished" if item.interval_minutes else "past_due")
            if item.interval_minutes is None and status == "past_due":
                oid = uuid5(NAMESPACE_URL, f"amora:agenda:occurrence:{item.id}:{utc(item.due_at).isoformat()}")
                with trace_lock(self.db._lock):
                    row = self.db._conn.execute(
                        "SELECT status,error_code FROM messaging_outbound_queue WHERE id=?",
                        (str(oid),),
                    ).fetchone()
                if row is not None:
                    status = ("delivered" if row["status"] == "sent" else
                              "uncertain" if row["error_code"] == "AGENDA_DELIVERY_UNCERTAIN" else
                              "queued" if row["status"] in {"queued", "claimed"} else "missed")
            out.append({"id": str(item.id), "text": item.text, "due_at": utc(item.due_at).isoformat(),
                        "timezone": item.timezone, "interval_minutes": item.interval_minutes,
                        "until_at": utc(item.until_at).isoformat() if item.until_at else None,
                        "next_due_at": due.isoformat() if due else None,
                        "status": status})
        return out


def agenda_claim_allowed(conn, message: OutboundMessage, now: datetime, *, require_connected=True):
    """Fail closed if cancelled, modified, expired, misrouted or unverified."""
    meta = message.metadata
    try:
        ident = UUID(meta["agenda_id"])
        due = utc(datetime.fromisoformat(meta["agenda_occurrence"]))
        expiry = utc(datetime.fromisoformat(meta["agenda_expires_at"]))
        external = meta["agenda_external_actor_id"]
        instant = utc(now)
        expected_id = uuid5(NAMESPACE_URL, f"amora:agenda:occurrence:{ident}:{due.isoformat()}")
        if (expected_id != message.id or instant < due or instant >= expiry
                or expiry != due + timedelta(minutes=5)
                or message.platform != MessagingPlatform.DISCORD or message.target_entity_id != OWNER):
            return False
        item = next((i for i in _ledger(conn) if i.id == ident), None)
        if item is None or item.status != "active" or occurrence(item, instant) != due:
            return False
        if (item.account_scope != message.account_scope or item.conversation_id != message.conversation_id
                or item.external_actor_id != external or item.text != message.text):
            return False
        return _valid_target(conn, item.account_scope, item.conversation_id, external,
                             connected=require_connected)
    except (ValueError, KeyError, TypeError, OverflowError):
        return False

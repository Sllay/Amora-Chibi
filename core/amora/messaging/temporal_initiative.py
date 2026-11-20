"""Bounded owner-DM initiative, using real conversation history and delivery.

The Core, not the language model, authorizes when and where to initiate.
Reservation in the existing event log is durable and at-most-once per
anchor/phase; uncertain generation is never automatically replayed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from amora.cognition import CognitiveRequest
from amora.cognition.temporal import aware_time, conversation_zone, opportunity_state
from amora.events import AmoraEvent, PrivacyScope
from amora.latency_trace import trace_lock

from .models import OutboundMessage, MessagingPlatform, OutboundStatus
from .session_identity import messaging_session_ref

_LOG = logging.getLogger("amora.temporal_initiative")
OWNER = "PERSON_BK"
ATTEMPT_TYPE = "conversation.temporal_attempt"
JOB_ID = "conversation.temporal_initiative"


def initiative_enabled():
    # This project's verified owner explicitly requested automatic contact.
    # Global kill switch; never extends authority to another person/channel.
    return os.getenv("AMORA_TEMPORAL_INITIATIVE", "1").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Anchor:
    audit_id: str
    outbound_id: str
    account_scope: str
    conversation_id: str
    session_id: str
    source_message_id: str
    native_hash: str
    revision: int
    user_at: object
    reply_at: object
    reply_text: str


def _json(row, key):
    value = json.loads(row[key] or "{}")
    return value if isinstance(value, dict) else {}


def read_anchor(conn, *, exclude_pending_id=None, now=None):
    """Read one latest verified owner DM; do not select an older eligible DM.

    Called under the canonical database lock, also inside the claim
    transaction. Native IDs stay here, never in the provider context.
    """
    audit = conn.execute(
        """SELECT * FROM messaging_audit WHERE platform='discord' AND channel_type='dm'
           AND direction='inbound' AND speaker_entity_id=?
           ORDER BY julianday(timestamp) DESC,id DESC LIMIT 1""", (OWNER,),
    ).fetchone()
    if audit is None or not audit["responded"] or audit["routing_action"] != "respond":
        return None
    user_at = aware_time(audit["timestamp"])
    if user_at is None or (now is not None and not 120 <= (now - user_at).total_seconds() <= 7 * 86400):
        return None
    account, conversation = audit["account_scope"], audit["conversation_id"]
    latest = conn.execute(
        """SELECT id FROM messaging_audit WHERE platform='discord' AND account_scope=?
           AND conversation_id=? AND direction='inbound'
           ORDER BY julianday(timestamp) DESC,id DESC LIMIT 1""", (account, conversation),
    ).fetchone()
    if latest is None or latest["id"] != audit["id"]:
        return None
    external = audit["sender_external_id"]
    if not isinstance(external, str) or not re.fullmatch(r"[0-9]{15,22}", external):
        return None
    binding = conn.execute(
        """SELECT entity_id,enabled,trust FROM platform_identity_bindings
           WHERE platform='discord' AND account_scope=? AND external_actor_id=?
             AND identifier_type IN ('user_id','discord_user_id')""", (account, external),
    ).fetchall()
    enabled = [row for row in binding if row["enabled"] == 1]
    if (not enabled or any(row["entity_id"] != OWNER for row in enabled)
            or not any(row["trust"] in {"operator_verified", "platform_authenticated"} for row in enabled)):
        return None
    presence = conn.execute(
        """SELECT 1 FROM platform_presence WHERE platform='discord' AND account_scope=?
           AND state='connected' AND desired_connected=1""", (account,),
    ).fetchone()
    if presence is None:
        return None
    meta = _json(audit, "metadata_json")
    turn_id, outbound_id = meta.get("deterministic_turn_id"), meta.get("deterministic_outbound_id")
    try:
        turn_id, outbound_id = str(UUID(turn_id)), str(UUID(outbound_id))
    except (ValueError, TypeError, AttributeError):
        return None
    session = messaging_session_ref("discord", account, conversation)
    source = conn.execute(
        """SELECT * FROM conversation_messages WHERE dedupe_key=? AND turn_id=?
           AND session_id=? AND speaker_id=? AND addressee_id='ENTITY_AMORA'
           AND status='received' AND privacy_scope='PERSONAL' AND remote_processing_allowed=1""",
        ("cognitive-input:" + turn_id, turn_id, session, OWNER),
    ).fetchone()
    if source is None:
        return None
    source_meta = _json(source, "metadata_json")
    if (source_meta.get("surface") != "platform_messaging" or source_meta.get("channel_type") != "dm"
            or source_meta.get("test_mode") or source_meta.get("observed_only")
            or source_meta.get("cognitive_mode") == "autonomous_initiation"):
        return None
    native_hash, revision = source_meta.get("native_message_id_hash"), source_meta.get("transport_revision")
    if (not isinstance(native_hash, str) or not re.fullmatch(r"[a-f0-9]{20}", native_hash)
            or type(revision) is not int or not 1 <= revision <= 1_000_000
            or native_hash != meta.get("native_message_id_hash") or revision != meta.get("transport_revision")):
        return None
    if conn.execute(
        """SELECT 1 FROM memory_reflection_invalidations WHERE session_id=?
           AND native_message_hash=? AND invalidated_through>=?""", (session, native_hash, revision),
    ).fetchone() is not None:
        return None
    # If the person resumed conversation on another surface, don't interrupt
    # them with a stale DM. Compare recorded receipt, not a user-supplied date.
    if conn.execute(
        """SELECT 1 FROM conversation_messages WHERE speaker_id=? AND status='received'
           AND julianday(started_at)>julianday(?) AND COALESCE(dedupe_key,'')<>?
           AND COALESCE(json_extract(metadata_json,'$.test_mode'),0)=0
           AND COALESCE(json_extract(metadata_json,'$.cognitive_mode'),'reply')<>'autonomous_initiation'
           LIMIT 1""", (OWNER, source["started_at"], "cognitive-input:" + turn_id),
    ).fetchone() is not None:
        return None
    reply = conn.execute(
        """SELECT * FROM messaging_outbound_queue WHERE id=? AND platform='discord'
           AND account_scope=? AND conversation_id=? AND target_entity_id=? AND status='sent'""",
        (outbound_id, account, conversation, OWNER),
    ).fetchone()
    if (reply is None or not reply["text"].strip() or not reply["source_message_id"]
            or hashlib.sha256(reply["source_message_id"].encode()).hexdigest()[:20] != native_hash):
        return None
    reply_meta = _json(reply, "metadata_json")
    if (reply_meta.get("fallback_used") or reply_meta.get("memory_disclosure")
            or reply_meta.get("local_control") or reply_meta.get("temporal_initiative")):
        return None
    pending = conn.execute(
        """SELECT 1 FROM messaging_outbound_queue WHERE platform='discord' AND account_scope=?
           AND conversation_id=? AND status IN ('queued','claimed') AND id<>? LIMIT 1""",
        (account, conversation, str(exclude_pending_id or "")),
    ).fetchone()
    if pending is not None:
        return None
    user_at = aware_time(audit["timestamp"])
    reply_at = aware_time(reply_meta.get("delivery_confirmed_at")) or aware_time(reply["created_at"])
    if user_at is None or reply_at is None:
        return None
    return Anchor(audit["id"], outbound_id, account, conversation, session,
                  reply["source_message_id"], native_hash, revision, user_at, reply_at, reply["text"])


def initiative_claim_allowed(conn, message, now):
    """Recheck authority, anchor and expiry immediately before worker claim."""
    if not initiative_enabled():
        return False
    meta = message.metadata
    expires = aware_time(meta.get("initiative_expires_at"))
    instant = aware_time(now)
    if (expires is None or instant is None or instant >= expires
            or not 8 <= instant.astimezone(conversation_zone()).hour < 22):
        return False
    anchor = read_anchor(conn, exclude_pending_id=message.id)
    return bool(anchor and anchor.audit_id == meta.get("initiative_anchor_id")
                and anchor.outbound_id == meta.get("initiative_reply_id")
                and anchor.account_scope == message.account_scope
                and anchor.conversation_id == message.conversation_id
                and anchor.session_id == meta.get("initiative_session_id")
                and message.target_entity_id == OWNER
                and message.platform == MessagingPlatform.DISCORD)


class TemporalInitiative:
    def __init__(self, core):
        self.core = core
        self.task = None

    def start(self):
        if initiative_enabled():
            self.core.scheduler.schedule_idle_interval(JOB_ID, 60, self._schedule)

    def _schedule(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run(), name=JOB_ID)

    async def _run(self):
        try:
            await self.tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # No raw content, native identifiers or exception payload in logs.
            _LOG.warning("temporal initiative failed: %s", type(exc).__name__)

    async def stop(self):
        self.core.scheduler.cancel(JOB_ID)
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def tick(self):
        """One bounded owner-DM opportunity, never a fake user request or a schedule."""
        if not initiative_enabled() or self.core.messaging._inflight:
            return None
        db = self.core.database
        now = self.core.clock.now()
        with trace_lock(db._lock):
            if not 8 <= now.astimezone(conversation_zone()).hour < 22:
                return None
            if db._conn.execute(
                "SELECT 1 FROM events WHERE event_type=? AND target=? AND timestamp>=? LIMIT 1",
                (ATTEMPT_TYPE, OWNER, (now - timedelta(hours=1)).isoformat()),
            ).fetchone():
                return None
            anchor = read_anchor(db._conn, now=now)
            if anchor is None:
                return None
            # BDI is trusted persistent Core state. The model may choose how
            # to express interest or remain silent, but cannot grant itself a DM.
            intention = self.core.autonomy.store.current_intention()
            intent_data = intention.model_dump(mode="json") if intention is not None else None
            opportunity = opportunity_state(
                now, anchor.user_at, anchor.reply_at, anchor.reply_text,
                intention=intent_data,
            )
            if opportunity is None:
                return None
            # A silent decision must not permanently disable initiative until
            # the six-hour window. Allow one further chance after 90 minutes,
            # still with the durable one-hour cooldown and the same DM fences.
            if opportunity["state"] == "long_absence":
                phase = "long"
            elif (now - anchor.reply_at).total_seconds() >= 90 * 60:
                phase = "late_short"
            else:
                phase = "short"
            key = f"amora:temporal-initiative:v1:{anchor.audit_id}:{phase}"
            event_id = uuid5(NAMESPACE_URL, key)
            if db._conn.execute("SELECT 1 FROM events WHERE dedupe_key=?", (key,)).fetchone():
                return None
        if not db.append_event(AmoraEvent(
            id=event_id, event_type=ATTEMPT_TYPE, timestamp=now, source="conversation_temporal_core",
            actor="ENTITY_AMORA", target=OWNER, session_id=anchor.session_id,
            privacy_scope=PrivacyScope.PERSONAL, dedupe_key=key,
            payload={"state": opportunity["state"], "phase": phase},
        )):
            return None
        request = CognitiveRequest(
            text="Oportunidade temporal interna da Amora: escolha conversar ou ficar em silêncio. Isto não é uma fala recebida.",
            speaker_id=OWNER, target_id="ENTITY_AMORA", session_id=anchor.session_id,
            turn_id=event_id, source_event_id=event_id, privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
            metadata={"surface": "platform_messaging", "channel_type": "dm", "platform": "discord",
                      "cognitive_mode": "autonomous_initiation", "temporal_initiative": True,
                      "temporal_opportunity": opportunity, "memory_consolidation": False,
                      "relationship_learning": False, "provider_total_budget_seconds": 40.0,
                      "provider_attempt_budget_seconds": 30.0},
        )
        await self.core.cognition.record_turn_decision_async(
            request, "TAKE_TURN", ["TEMPORAL_OPPORTUNITY", "SILENCE_ALLOWED"], 0.6,
        )
        try:
            plan = await asyncio.wait_for(self.core.cognition.respond(request), timeout=55)
        except TimeoutError:
            return None
        if (plan.fallback_used or not plan.provider or not plan.display_text.strip()
                or len(plan.display_text) > 360 or "TEMPORAL_SILENCE_CHOSEN" in plan.reason_codes):
            return None
        outbound = OutboundMessage(
            id=event_id, platform=MessagingPlatform.DISCORD, account_scope=anchor.account_scope,
            conversation_id=anchor.conversation_id, target_entity_id=OWNER,
            text=plan.display_text.strip(), source_message_id=anchor.source_message_id,
            created_at=self.core.clock.now(), status=OutboundStatus.QUEUED,
            metadata={"temporal_initiative": True, "initiative_anchor_id": anchor.audit_id,
                      "initiative_reply_id": anchor.outbound_id, "initiative_session_id": anchor.session_id,
                      "initiative_expires_at": (self.core.clock.now() + timedelta(minutes=5)).isoformat(),
                      "transport_revision": anchor.revision, "cognitive_turn_id": str(event_id),
                      "initiative_state": opportunity["state"], "provider": plan.provider, "model": plan.model},
        )
        with trace_lock(db._lock):
            if self.core.messaging._inflight or not initiative_claim_allowed(db._conn, outbound, self.core.clock.now()):
                return None
        if self.core.messaging_store.put_outbound(outbound):
            await self.core.cognition.emit_event(plan)
            return outbound
        return None

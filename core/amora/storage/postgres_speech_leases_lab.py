"""Phase 2E.3: synthetic PostgreSQL speech queue, never production.

Mirrors ConversationStore's Pydantic SpeechUtterance enqueue, claim, ack,
cancel and cognitive-turn voice-stop semantics. Uses PostgreSQL FOR UPDATE
SKIP LOCKED so independent workers cannot claim the same row.
Only a caller supplied loopback PG18 amora_pg_synthetic connection works.
No DSN, real memory path, production backend selector or Aiven traffic.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from uuid import UUID

from amora.conversation.models import SpeechUtterance, UtteranceStatus

from .postgres_memory_lab import ensure_local_lab


class SpeechLabError(ValueError):
    pass


_TURN = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")


def _stamp(now: datetime) -> str:
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise SpeechLabError("SPEECH_LAB_AWARE_TIME_REQUIRED")
    return now.isoformat()


def _json(item: SpeechUtterance) -> str:
    return json.dumps(item.model_dump(mode="json"), ensure_ascii=False,
                      separators=(",", ":"))


def _from_json(payload: str) -> SpeechUtterance:
    return SpeechUtterance.model_validate(json.loads(payload))


def _save(conn, item: SpeechUtterance) -> None:
    result = conn.execute("""
        UPDATE amora_pg_v18_lab.speech_utterances
        SET status=%s, utterance_json=%s, claimed_at=%s,
            claim_expires_at=%s, spoken_at=%s
        WHERE id=%s
    """, (
        item.status.value, _json(item),
        item.claimed_at.isoformat() if item.claimed_at else None,
        item.claim_expires_at.isoformat() if item.claim_expires_at else None,
        item.spoken_at.isoformat() if item.spoken_at else None,
        str(item.id),
    ))
    if result.rowcount != 1:
        raise SpeechLabError("SPEECH_LAB_CONCURRENT_STATE_CHANGED")


def enqueue(conn, item: SpeechUtterance) -> bool:
    """Insert-only speech with voice-stop replay detection."""
    ensure_local_lab(conn)
    if not isinstance(item, SpeechUtterance):
        raise SpeechLabError("SPEECH_LAB_INVALID_UTTERANCE")
    with conn.transaction():
        turn_id = item.metadata.get("cognitive_turn_id")
        if turn_id:
            turn = str(turn_id)
            if not _TURN.fullmatch(turn):
                raise SpeechLabError("SPEECH_LAB_INVALID_TURN_ID")
            prefix = f"speech:cognitive:{turn}:"
            rows = conn.execute("""
                SELECT utterance_json FROM amora_pg_v18_lab.speech_utterances
                WHERE device_id=%s AND status='cancelled'
                  AND LEFT(dedupe_key,LENGTH(%s))=%s
            """, (item.device_id, prefix, prefix)).fetchall()
            for (payload,) in rows:
                previous = _from_json(payload)
                if previous.metadata.get("voice_cancelled_turn") == turn:
                    item.status = UtteranceStatus.CANCELLED
                    item.metadata.update({
                        "voice_cancelled_turn": turn,
                        "cancel_reason": previous.metadata.get(
                            "cancel_reason", "voice_cancelled"),
                    })
                    break
        result = conn.execute("""
            INSERT INTO amora_pg_v18_lab.speech_utterances(
                id,target_id,kind,priority,privacy_scope,device_id,status,
                utterance_json,created_at,claimed_at,claim_expires_at,
                spoken_at,dedupe_key
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING RETURNING id
        """, (
            str(item.id), item.target_id, item.kind.value, item.priority,
            item.privacy_scope.value, item.device_id, item.status.value,
            _json(item), item.created_at.isoformat(),
            item.claimed_at.isoformat() if item.claimed_at else None,
            item.claim_expires_at.isoformat() if item.claim_expires_at else None,
            item.spoken_at.isoformat() if item.spoken_at else None,
            item.dedupe_key,
        )).fetchone()
    return result is not None


def get_utterance(conn, utterance_id: UUID | str) -> SpeechUtterance | None:
    ensure_local_lab(conn)
    row = conn.execute("""
        SELECT utterance_json FROM amora_pg_v18_lab.speech_utterances
        WHERE id=%s
    """, (str(utterance_id),)).fetchone()
    return _from_json(row[0]) if row else None


def claim_next(conn, device_id: str, now: datetime, *, lease_seconds: int = 30,
               cognitive_turn_id: UUID | str | None = None) -> SpeechUtterance | None:
    """Expire old leases and atomically claim one selected queued item."""
    ensure_local_lab(conn)
    now_text = _stamp(now)
    expires = now + timedelta(seconds=max(5, min(180, int(lease_seconds))))
    turn_filter = str(cognitive_turn_id) if cognitive_turn_id is not None else None
    if turn_filter is not None and not _TURN.fullmatch(turn_filter):
        raise SpeechLabError("SPEECH_LAB_INVALID_TURN_ID")
    with conn.transaction():
        stale = conn.execute("""
            SELECT utterance_json FROM amora_pg_v18_lab.speech_utterances
            WHERE status='claimed' AND claim_expires_at IS NOT NULL
              AND claim_expires_at<=%s
            ORDER BY claim_expires_at,id FOR UPDATE SKIP LOCKED
        """, (now_text,)).fetchall()
        for (payload,) in stale:
            item = _from_json(payload)
            item.status = UtteranceStatus.QUEUED
            item.claimed_at = None
            item.claim_expires_at = None
            _save(conn, item)
        rows = conn.execute("""
            SELECT utterance_json FROM amora_pg_v18_lab.speech_utterances
            WHERE device_id=%s AND status='queued'
            ORDER BY priority DESC,created_at ASC,id ASC
            LIMIT %s FOR UPDATE SKIP LOCKED
        """, (device_id, 32 if turn_filter is not None else 1)).fetchall()
        for (payload,) in rows:
            item = _from_json(payload)
            if turn_filter is not None and str(
                item.metadata.get("cognitive_turn_id") or ""
            ) != turn_filter:
                continue
            item.status = UtteranceStatus.CLAIMED
            item.claimed_at = now
            item.claim_expires_at = expires
            _save(conn, item)
            return item
    return None


def acknowledge(conn, utterance_id: UUID | str, now: datetime, *,
                spoken: bool) -> SpeechUtterance | None:
    ensure_local_lab(conn)
    _stamp(now)
    with conn.transaction():
        row = conn.execute("""
            SELECT utterance_json FROM amora_pg_v18_lab.speech_utterances
            WHERE id=%s FOR UPDATE
        """, (str(utterance_id),)).fetchone()
        if row is None:
            return None
        item = _from_json(row[0])
        if item.status in (UtteranceStatus.SPOKEN, UtteranceStatus.CANCELLED):
            return item
        if item.status not in (UtteranceStatus.CLAIMED, UtteranceStatus.QUEUED):
            return item
        if spoken:
            item.status = UtteranceStatus.SPOKEN
            item.spoken_at = now
            item.claim_expires_at = None
        else:
            item.status = UtteranceStatus.QUEUED
            item.claimed_at = None
            item.claim_expires_at = None
        _save(conn, item)
        return item


def cancel(conn, utterance_id: UUID | str, now: datetime, *,
           reason: str = "cancelled") -> SpeechUtterance | None:
    ensure_local_lab(conn)
    _stamp(now)
    with conn.transaction():
        row = conn.execute("""
            SELECT utterance_json FROM amora_pg_v18_lab.speech_utterances
            WHERE id=%s FOR UPDATE
        """, (str(utterance_id),)).fetchone()
        if row is None:
            return None
        item = _from_json(row[0])
        if item.status in (UtteranceStatus.SPOKEN, UtteranceStatus.CANCELLED):
            return item
        item.status = UtteranceStatus.CANCELLED
        item.claim_expires_at = None
        item.metadata["cancel_reason"] = reason[:96]
        item.metadata["cancelled_at"] = now.isoformat()
        _save(conn, item)
        return item


def cancel_cognitive_turn(conn, turn_id: UUID | str, now: datetime, *,
                          reason: str = "barge_in",
                          speech_only: bool = False) -> int:
    ensure_local_lab(conn)
    _stamp(now)
    turn = str(turn_id)
    if not _TURN.fullmatch(turn):
        raise SpeechLabError("SPEECH_LAB_INVALID_TURN_ID")
    changed = 0
    with conn.transaction():
        rows = conn.execute("""
            SELECT utterance_json FROM amora_pg_v18_lab.speech_utterances
            WHERE status IN ('queued','claimed')
            ORDER BY id FOR UPDATE
        """).fetchall()
        for (payload,) in rows:
            item = _from_json(payload)
            if str(item.metadata.get("cognitive_turn_id") or "") != turn:
                continue
            item.status = UtteranceStatus.CANCELLED
            item.claim_expires_at = None
            item.metadata["cancel_reason"] = reason[:96]
            item.metadata["cancelled_at"] = now.isoformat()
            if speech_only:
                item.metadata["voice_cancelled_turn"] = turn
            _save(conn, item)
            changed += 1
    return changed

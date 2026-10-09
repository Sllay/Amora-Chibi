from __future__ import annotations

import json
import sqlite3
import threading
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from amora.time import Clock

from .models import SpeechUtterance, TurnDecision, UtteranceStatus


class ConversationStore:
    def __init__(self, path: Path, clock: Clock) -> None:
        self.path = path
        self.clock = clock
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def put_turn_decision(self, item: TurnDecision) -> bool:
        payload = json.dumps(item.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    """
                    INSERT INTO conversation_turn_decisions(
                        id, state, score, result_json, created_at, dedupe_key
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(item.id), item.state.value, item.score, payload,
                        item.created_at.isoformat(), item.dedupe_key,
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def get_turn_by_dedupe(self, dedupe_key: str) -> TurnDecision | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT result_json FROM conversation_turn_decisions WHERE dedupe_key = ?",
                (dedupe_key,),
            ).fetchone()
        if row is None:
            return None
        result = TurnDecision.model_validate(json.loads(row["result_json"]))
        result.deduplicated = True
        return result

    def list_turns(self, limit: int = 50) -> list[TurnDecision]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT result_json FROM conversation_turn_decisions ORDER BY created_at DESC LIMIT ?",
                (max(1, min(200, int(limit))),),
            ).fetchall()
        return [TurnDecision.model_validate(json.loads(row["result_json"])) for row in rows]

    def turn_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM conversation_turn_decisions").fetchone()
        return int(row[0]) if row else 0

    def enqueue(self, item: SpeechUtterance) -> bool:
        payload = json.dumps(item.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    """
                    INSERT INTO speech_utterances(
                        id, target_id, kind, priority, privacy_scope, device_id, status,
                        utterance_json, created_at, claimed_at, claim_expires_at, spoken_at, dedupe_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(item.id), item.target_id, item.kind.value, item.priority,
                        item.privacy_scope.value, item.device_id, item.status.value, payload,
                        item.created_at.isoformat(),
                        item.claimed_at.isoformat() if item.claimed_at else None,
                        item.claim_expires_at.isoformat() if item.claim_expires_at else None,
                        item.spoken_at.isoformat() if item.spoken_at else None,
                        item.dedupe_key,
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def get_utterance_by_dedupe(self, dedupe_key: str) -> SpeechUtterance | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT utterance_json FROM speech_utterances WHERE dedupe_key = ?",
                (dedupe_key,),
            ).fetchone()
        return SpeechUtterance.model_validate(json.loads(row["utterance_json"])) if row else None

    def get_utterance(self, utterance_id: UUID | str) -> SpeechUtterance | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT utterance_json FROM speech_utterances WHERE id = ?",
                (str(utterance_id),),
            ).fetchone()
        return SpeechUtterance.model_validate(json.loads(row["utterance_json"])) if row else None

    def list_utterances(self, limit: int = 50) -> list[SpeechUtterance]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT utterance_json FROM speech_utterances ORDER BY created_at DESC LIMIT ?",
                (max(1, min(200, int(limit))),),
            ).fetchall()
        return [SpeechUtterance.model_validate(json.loads(row["utterance_json"])) for row in rows]

    def pending_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM speech_utterances WHERE status IN (?, ?)",
                (UtteranceStatus.QUEUED.value, UtteranceStatus.CLAIMED.value),
            ).fetchone()
        return int(row[0]) if row else 0

    def spoken_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM speech_utterances WHERE status = ?",
                (UtteranceStatus.SPOKEN.value,),
            ).fetchone()
        return int(row[0]) if row else 0

    def claim_next(self, device_id: str, *, lease_seconds: int = 30) -> SpeechUtterance | None:
        now = self.clock.now()
        expires = now + timedelta(seconds=max(5, min(120, int(lease_seconds))))
        now_text = now.isoformat()
        with self._lock, self._conn:
            stale = self._conn.execute(
                """
                SELECT id, utterance_json FROM speech_utterances
                WHERE status = ? AND claim_expires_at IS NOT NULL AND claim_expires_at <= ?
                """,
                (UtteranceStatus.CLAIMED.value, now_text),
            ).fetchall()
            for row in stale:
                item = SpeechUtterance.model_validate(json.loads(row["utterance_json"]))
                item.status = UtteranceStatus.QUEUED
                item.claimed_at = None
                item.claim_expires_at = None
                self._update_utterance(item)

            row = self._conn.execute(
                """
                SELECT id, utterance_json FROM speech_utterances
                WHERE device_id = ? AND status = ?
                ORDER BY priority DESC, created_at ASC LIMIT 1
                """,
                (device_id, UtteranceStatus.QUEUED.value),
            ).fetchone()
            if row is None:
                return None
            item = SpeechUtterance.model_validate(json.loads(row["utterance_json"]))
            item.status = UtteranceStatus.CLAIMED
            item.claimed_at = now
            item.claim_expires_at = expires
            self._update_utterance(item)
            return item

    def acknowledge(self, utterance_id: UUID | str, *, spoken: bool) -> SpeechUtterance | None:
        item = self.get_utterance(utterance_id)
        if item is None:
            return None
        if item.status == UtteranceStatus.SPOKEN:
            return item
        if item.status not in {UtteranceStatus.CLAIMED, UtteranceStatus.QUEUED}:
            return item
        if spoken:
            item.status = UtteranceStatus.SPOKEN
            item.spoken_at = self.clock.now()
            item.claim_expires_at = None
        else:
            item.status = UtteranceStatus.QUEUED
            item.claimed_at = None
            item.claim_expires_at = None
        with self._lock, self._conn:
            self._update_utterance(item)
        return item

    def _update_utterance(self, item: SpeechUtterance) -> None:
        payload = json.dumps(item.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        self._conn.execute(
            """
            UPDATE speech_utterances SET
                status = ?, utterance_json = ?, claimed_at = ?, claim_expires_at = ?, spoken_at = ?
            WHERE id = ?
            """,
            (
                item.status.value,
                payload,
                item.claimed_at.isoformat() if item.claimed_at else None,
                item.claim_expires_at.isoformat() if item.claim_expires_at else None,
                item.spoken_at.isoformat() if item.spoken_at else None,
                str(item.id),
            ),
        )

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from uuid import UUID

from amora.events import PrivacyScope
from amora.time import Clock

from .models import (
    AppraisalInput,
    EmotionEvent,
    EmotionType,
    EntityAffect,
    MoodState,
    SocialBid,
    SocialBidStatus,
    SocialBidType,
)


class EmotionStore:
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

    def event_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM emotion_events").fetchone()
        return int(row[0]) if row else 0

    def put_event(self, event: EmotionEvent) -> bool:
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    """
                    INSERT INTO emotion_events(
                        id, emotion, intensity, cause_event_id, cause_description,
                        target_id, agent_id, blame, intentionality, spillover,
                        appraisal_json, privacy_scope, created_at, dedupe_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(event.id), event.emotion.value, event.intensity,
                        str(event.cause_event_id) if event.cause_event_id else None,
                        event.cause_description, event.target_id, event.agent_id,
                        event.blame, event.intentionality, event.spillover,
                        json.dumps(event.appraisal.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")),
                        event.privacy_scope.value, event.created_at.isoformat(), event.dedupe_key,
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def get_event(self, event_id: UUID | str) -> EmotionEvent | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM emotion_events WHERE id = ?", (str(event_id),)).fetchone()
        return self._hydrate_event(row) if row else None

    def get_event_by_dedupe(self, dedupe_key: str) -> EmotionEvent | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM emotion_events WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
        return self._hydrate_event(row) if row else None

    def list_events(self, *, target_id: str | None = None, limit: int = 50) -> list[EmotionEvent]:
        sql = "SELECT * FROM emotion_events"
        params: list[object] = []
        if target_id is not None:
            sql += " WHERE target_id = ?"
            params.append(target_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(200, int(limit))))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._hydrate_event(row) for row in rows]

    def get_mood(self) -> MoodState:
        with self._lock:
            row = self._conn.execute("SELECT * FROM mood_state WHERE singleton_id = 1").fetchone()
        if row is None:
            return MoodState(updated_at=self.clock.now())
        return MoodState(
            valence=float(row["valence"]),
            arousal=float(row["arousal"]),
            dominance=float(row["dominance"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def put_mood(self, mood: MoodState) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO mood_state(singleton_id, valence, arousal, dominance, updated_at)
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    valence=excluded.valence,
                    arousal=excluded.arousal,
                    dominance=excluded.dominance,
                    updated_at=excluded.updated_at
                """,
                (mood.valence, mood.arousal, mood.dominance, mood.updated_at.isoformat()),
            )

    def get_affect(self, entity_id: str) -> EntityAffect | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM entity_affect WHERE entity_id = ?", (entity_id,)).fetchone()
        if row is None:
            return None
        return self._hydrate_affect(row)

    def put_affect(self, affect: EntityAffect) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO entity_affect(
                    entity_id, anger, affection, trust, irritation, fear, gratitude, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_id) DO UPDATE SET
                    anger=excluded.anger,
                    affection=excluded.affection,
                    trust=excluded.trust,
                    irritation=excluded.irritation,
                    fear=excluded.fear,
                    gratitude=excluded.gratitude,
                    updated_at=excluded.updated_at
                """,
                (
                    affect.entity_id, affect.anger, affect.affection, affect.trust,
                    affect.irritation, affect.fear, affect.gratitude, affect.updated_at.isoformat(),
                ),
            )

    def list_affect(self) -> list[EntityAffect]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM entity_affect ORDER BY entity_id").fetchall()
        return [self._hydrate_affect(row) for row in rows]

    def put_bid(self, bid: SocialBid) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO social_bids(
                    id, bid_type, target_id, strength, reason, source_emotion_event_id,
                    created_at, expires_at, status, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(bid.id), bid.bid_type.value, bid.target_id, bid.strength, bid.reason,
                    str(bid.source_emotion_event_id), bid.created_at.isoformat(), bid.expires_at.isoformat(),
                    bid.status.value, json.dumps(bid.metadata, ensure_ascii=False, separators=(",", ":")),
                ),
            )

    def list_bids(self, *, include_expired: bool = False, limit: int = 50) -> list[SocialBid]:
        now = self.clock.now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE social_bids SET status = ? WHERE status = ? AND expires_at <= ?",
                (SocialBidStatus.EXPIRED.value, SocialBidStatus.PENDING.value, now),
            )
            sql = "SELECT * FROM social_bids"
            params: list[object] = []
            if not include_expired:
                sql += " WHERE status = ?"
                params.append(SocialBidStatus.PENDING.value)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(max(1, min(200, int(limit))))
            rows = self._conn.execute(sql, params).fetchall()
        return [self._hydrate_bid(row) for row in rows]

    def set_bid_status(self, bid_id: UUID | str, status: SocialBidStatus) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("UPDATE social_bids SET status = ? WHERE id = ?", (status.value, str(bid_id)))
        return cur.rowcount > 0

    def _hydrate_event(self, row: sqlite3.Row) -> EmotionEvent:
        appraisal = AppraisalInput.model_validate(json.loads(row["appraisal_json"]))
        return EmotionEvent(
            id=UUID(row["id"]), emotion=EmotionType(row["emotion"]), intensity=float(row["intensity"]),
            cause_event_id=UUID(row["cause_event_id"]) if row["cause_event_id"] else None,
            cause_description=row["cause_description"], target_id=row["target_id"], agent_id=row["agent_id"],
            blame=float(row["blame"]), intentionality=float(row["intentionality"]), spillover=float(row["spillover"]),
            appraisal=appraisal, privacy_scope=PrivacyScope(row["privacy_scope"]),
            created_at=datetime.fromisoformat(row["created_at"]), dedupe_key=row["dedupe_key"],
        )

    @staticmethod
    def _hydrate_affect(row: sqlite3.Row) -> EntityAffect:
        return EntityAffect(
            entity_id=row["entity_id"], anger=float(row["anger"]), affection=float(row["affection"]),
            trust=float(row["trust"]), irritation=float(row["irritation"]), fear=float(row["fear"]),
            gratitude=float(row["gratitude"]), updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _hydrate_bid(row: sqlite3.Row) -> SocialBid:
        return SocialBid(
            id=UUID(row["id"]), bid_type=SocialBidType(row["bid_type"]), target_id=row["target_id"],
            strength=float(row["strength"]), reason=row["reason"], source_emotion_event_id=UUID(row["source_emotion_event_id"]),
            created_at=datetime.fromisoformat(row["created_at"]), expires_at=datetime.fromisoformat(row["expires_at"]),
            status=SocialBidStatus(row["status"]), metadata=json.loads(row["metadata_json"]),
        )

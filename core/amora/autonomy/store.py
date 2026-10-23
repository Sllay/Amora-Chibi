from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from uuid import UUID

from amora.storage.backend import SQLiteBackend, StorageBackend
from amora.time import Clock

from .models import (
    AutonomyCycleResult,
    DecisionRecord,
    DriveAdjustment,
    DriveState,
    DriveType,
    Intention,
    IntentionStatus,
)


class AutonomyStore:
    def __init__(self, path: Path, clock: Clock, *, backend: StorageBackend | None = None) -> None:
        self.path = path
        self.clock = clock
        self._lock = threading.RLock()
        self.backend = backend or SQLiteBackend()
        self._conn = self.backend.connect(path)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def decision_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM autonomy_decisions").fetchone()
        return int(row[0]) if row else 0

    def ensure_drive(self, state: DriveState) -> DriveState:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO autonomy_drives(drive, need, baseline, growth_per_hour, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(drive) DO NOTHING
                """,
                (state.drive.value, state.need, state.baseline, state.growth_per_hour, state.updated_at.isoformat()),
            )
        return self.get_drive(state.drive) or state

    def get_drive(self, drive: DriveType) -> DriveState | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM autonomy_drives WHERE drive = ?", (drive.value,)).fetchone()
        return self._hydrate_drive(row) if row else None

    def list_drives(self) -> list[DriveState]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM autonomy_drives ORDER BY drive").fetchall()
        return [self._hydrate_drive(row) for row in rows]

    def put_drive(self, state: DriveState) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO autonomy_drives(drive, need, baseline, growth_per_hour, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(drive) DO UPDATE SET
                    need=excluded.need,
                    baseline=excluded.baseline,
                    growth_per_hour=excluded.growth_per_hour,
                    updated_at=excluded.updated_at
                """,
                (state.drive.value, state.need, state.baseline, state.growth_per_hour, state.updated_at.isoformat()),
            )

    def put_adjustment(self, item: DriveAdjustment) -> bool:
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    """
                    INSERT INTO drive_adjustments(id, drive, delta, reason, source_event_id, created_at, dedupe_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(item.id), item.drive.value, item.delta, item.reason,
                        str(item.source_event_id) if item.source_event_id else None,
                        item.created_at.isoformat(), item.dedupe_key,
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def get_adjustment_by_dedupe(self, dedupe_key: str) -> DriveAdjustment | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM drive_adjustments WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
        return self._hydrate_adjustment(row) if row else None

    def current_intention(self) -> Intention | None:
        now = self.clock.now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE autonomy_intentions SET status = ?, updated_at = ? WHERE status = ? AND expires_at IS NOT NULL AND expires_at <= ?",
                (IntentionStatus.EXPIRED.value, now, IntentionStatus.ACTIVE.value, now),
            )
            row = self._conn.execute(
                "SELECT * FROM autonomy_intentions WHERE status = ? ORDER BY created_at DESC LIMIT 1",
                (IntentionStatus.ACTIVE.value,),
            ).fetchone()
        return self._hydrate_intention(row) if row else None

    def put_intention(self, item: Intention) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO autonomy_intentions(
                    id, kind, target_id, source_kind, source_ref_id, selected_utility,
                    urgency, commitment_strength, created_at, updated_at, expires_at,
                    status, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    selected_utility=excluded.selected_utility,
                    urgency=excluded.urgency,
                    commitment_strength=excluded.commitment_strength,
                    updated_at=excluded.updated_at,
                    expires_at=excluded.expires_at,
                    status=excluded.status,
                    metadata_json=excluded.metadata_json
                """,
                (
                    str(item.id), item.kind.value, item.target_id, item.source_kind, item.source_ref_id,
                    item.selected_utility, item.urgency, item.commitment_strength,
                    item.created_at.isoformat(), item.updated_at.isoformat(),
                    item.expires_at.isoformat() if item.expires_at else None,
                    item.status.value,
                    json.dumps(item.metadata, ensure_ascii=False, separators=(",", ":")),
                ),
            )

    def set_intention_status(self, intention_id: UUID | str, status: IntentionStatus) -> bool:
        now = self.clock.now().isoformat()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE autonomy_intentions SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, now, str(intention_id)),
            )
        return cur.rowcount > 0

    def put_cycle_result(self, result: AutonomyCycleResult) -> bool:
        decision = result.decision
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    """
                    INSERT INTO autonomy_decisions(
                        id, outcome, selected_intention_id, previous_intention_id,
                        selected_utility, silence_utility, reason_codes_json,
                        result_json, created_at, dedupe_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(decision.id), decision.outcome.value,
                        str(decision.selected_intention_id) if decision.selected_intention_id else None,
                        str(decision.previous_intention_id) if decision.previous_intention_id else None,
                        decision.selected_utility, decision.silence_utility,
                        json.dumps(decision.reason_codes, ensure_ascii=False, separators=(",", ":")),
                        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")),
                        decision.created_at.isoformat(), decision.dedupe_key,
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def get_cycle_by_dedupe(self, dedupe_key: str) -> AutonomyCycleResult | None:
        with self._lock:
            row = self._conn.execute("SELECT result_json FROM autonomy_decisions WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
        if row is None:
            return None
        result = AutonomyCycleResult.model_validate(json.loads(row["result_json"]))
        result.deduplicated = True
        return result

    def list_decisions(self, limit: int = 50) -> list[DecisionRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM autonomy_decisions ORDER BY created_at DESC LIMIT ?",
                (max(1, min(200, int(limit))),),
            ).fetchall()
        return [
            DecisionRecord(
                id=UUID(row["id"]),
                outcome=row["outcome"],
                selected_intention_id=UUID(row["selected_intention_id"]) if row["selected_intention_id"] else None,
                previous_intention_id=UUID(row["previous_intention_id"]) if row["previous_intention_id"] else None,
                selected_utility=float(row["selected_utility"]) if row["selected_utility"] is not None else None,
                silence_utility=float(row["silence_utility"]),
                reason_codes=json.loads(row["reason_codes_json"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                dedupe_key=row["dedupe_key"],
            )
            for row in rows
        ]

    @staticmethod
    def _hydrate_drive(row: sqlite3.Row) -> DriveState:
        return DriveState(
            drive=DriveType(row["drive"]),
            need=float(row["need"]),
            baseline=float(row["baseline"]),
            growth_per_hour=float(row["growth_per_hour"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _hydrate_adjustment(row: sqlite3.Row) -> DriveAdjustment:
        return DriveAdjustment(
            id=UUID(row["id"]),
            drive=DriveType(row["drive"]),
            delta=float(row["delta"]),
            reason=row["reason"],
            source_event_id=UUID(row["source_event_id"]) if row["source_event_id"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            dedupe_key=row["dedupe_key"],
        )

    @staticmethod
    def _hydrate_intention(row: sqlite3.Row) -> Intention:
        return Intention(
            id=UUID(row["id"]), kind=row["kind"], target_id=row["target_id"],
            source_kind=row["source_kind"], source_ref_id=row["source_ref_id"],
            selected_utility=float(row["selected_utility"]), urgency=float(row["urgency"]),
            commitment_strength=float(row["commitment_strength"]),
            created_at=datetime.fromisoformat(row["created_at"]), updated_at=datetime.fromisoformat(row["updated_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
            status=row["status"], metadata=json.loads(row["metadata_json"]),
        )

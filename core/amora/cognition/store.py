from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from uuid import UUID

from amora.events import PrivacyScope
from amora.storage.backend import SQLiteBackend, StorageBackend, execute_atomic_statement, is_duplicate_error
from amora.time import Clock

from .models import CognitiveJournalEntry, ConversationMessage, MessageSource, MessageStatus, SpeechSegment


class CognitiveStore:
    """Persistent conversation/journal store.

    AUTH/SECRET content is intentionally redacted before persistence. The row can
    still prove that a stage occurred without turning observability into a secret log.
    """

    def __init__(self, database_path: Path, clock: Clock, *, backend: StorageBackend | None = None) -> None:
        self.database_path = database_path
        self.clock = clock
        self._lock = threading.RLock()
        self.backend = backend or SQLiteBackend()
        self._conn = self.backend.connect(database_path)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _is_redacted(scope: PrivacyScope) -> bool:
        return scope in {PrivacyScope.SECRET, PrivacyScope.AUTH}

    def put_message(self, item: ConversationMessage) -> bool:
        redacted = self._is_redacted(item.privacy_scope)
        metadata = dict(item.metadata)
        if redacted:
            metadata["content_redacted"] = True
        values = (
            str(item.id), str(item.turn_id), item.speaker_id, item.addressee_id, item.source.value,
            None if redacted else item.raw_text,
            None if redacted else item.normalized_text,
            None if redacted else item.spoken_text,
            item.privacy_scope.value, int(item.remote_processing_allowed and not redacted), item.stt_confidence,
            item.provider, item.model, item.status.value, int(item.interrupted),
            str(item.source_event_id) if item.source_event_id else None, item.session_id,
            item.started_at.isoformat(), item.finished_at.isoformat() if item.finished_at else None,
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), item.dedupe_key,
        )
        with self._lock:
            try:
                execute_atomic_statement(
                    self.backend,
                    self._conn,
                    """INSERT INTO conversation_messages(
                        id, turn_id, speaker_id, addressee_id, source, raw_text, normalized_text, spoken_text,
                        privacy_scope, remote_processing_allowed, stt_confidence, provider, model, status, interrupted,
                        source_event_id, session_id, started_at, finished_at, metadata_json, dedupe_key
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    values,
                )
            except sqlite3.IntegrityError as exc:
                if not is_duplicate_error(exc):
                    raise
                return False
        return True

    def get_message_by_dedupe(self, dedupe_key: str) -> ConversationMessage | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM conversation_messages WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
        return self._message_from_row(row) if row else None

    def list_messages(self, *, limit: int = 40, speaker_id: str | None = None, turn_id: UUID | str | None = None) -> list[ConversationMessage]:
        clauses: list[str] = []
        args: list[object] = []
        if speaker_id is not None:
            clauses.append("speaker_id = ?")
            args.append(speaker_id)
        if turn_id is not None:
            clauses.append("turn_id = ?")
            args.append(str(turn_id))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        args.append(max(1, min(500, int(limit))))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM conversation_messages{where} ORDER BY started_at DESC LIMIT ?", tuple(args)
            ).fetchall()
        return [self._message_from_row(row) for row in reversed(rows)]

    def update_message_delivery(
        self,
        message_id: UUID | str,
        *,
        status: MessageStatus,
        spoken_text: str | None = None,
        interrupted: bool | None = None,
    ) -> bool:
        with self._lock, self._conn:
            row = self._conn.execute("SELECT privacy_scope FROM conversation_messages WHERE id = ?", (str(message_id),)).fetchone()
            if row is None:
                return False
            redacted = row["privacy_scope"] in {PrivacyScope.SECRET.value, PrivacyScope.AUTH.value}
            cur = self._conn.execute(
                """UPDATE conversation_messages
                   SET status = ?, spoken_text = COALESCE(?, spoken_text), interrupted = COALESCE(?, interrupted),
                       finished_at = CASE WHEN ? IN ('spoken','interrupted','failed') THEN ? ELSE finished_at END
                   WHERE id = ?""",
                (
                    status.value,
                    None if redacted else spoken_text,
                    None if interrupted is None else int(interrupted),
                    status.value,
                    self.clock.now().isoformat(),
                    str(message_id),
                ),
            )
            return cur.rowcount > 0

    def put_journal(self, entry: CognitiveJournalEntry) -> bool:
        redacted = self._is_redacted(entry.privacy_scope)
        summary = "REDACTED" if redacted and entry.summary else entry.summary
        payload = {} if redacted else entry.payload
        metadata = dict(entry.metadata)
        if redacted:
            metadata["content_redacted"] = True
        values = (
            str(entry.id), str(entry.turn_id), entry.stage.value, entry.timestamp.isoformat(), entry.source,
            summary, json.dumps(entry.reason_codes, ensure_ascii=False),
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")), entry.privacy_scope.value,
            entry.provider, entry.model, entry.duration_ms,
            str(entry.correlation_id) if entry.correlation_id else None, entry.error_code,
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), entry.dedupe_key,
        )
        with self._lock:
            cursor = execute_atomic_statement(
                self.backend,
                self._conn,
                """INSERT INTO cognitive_journal(
                    id, turn_id, stage, timestamp, source, summary, reason_codes_json, payload_json,
                    privacy_scope, provider, model, duration_ms, correlation_id, error_code, metadata_json, dedupe_key
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(dedupe_key) DO NOTHING""",
                values,
            )
            if cursor.rowcount not in {0, 1}:
                raise sqlite3.DatabaseError("SQL_UNEXPECTED_ROWCOUNT")
        # Do not report an inserted/no-op result until the atomic statement succeeded.
        return cursor.rowcount == 1

    def journal_for_turn(self, turn_id: UUID | str) -> list[CognitiveJournalEntry]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM cognitive_journal WHERE turn_id = ? ORDER BY timestamp, rowid", (str(turn_id),)
            ).fetchall()
        return [self._journal_from_row(row) for row in rows]

    def recent_journal(self, *, limit: int = 100) -> list[CognitiveJournalEntry]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM cognitive_journal ORDER BY timestamp DESC LIMIT ?", (max(1, min(1000, int(limit))),)
            ).fetchall()
        return [self._journal_from_row(row) for row in reversed(rows)]

    def put_segment(self, segment: SpeechSegment) -> bool:
        with self._lock:
            try:
                execute_atomic_statement(
                    self.backend,
                    self._conn,
                    """INSERT INTO speech_segments(
                        id, turn_id, ordinal, display_text, speech_text, status, tts_provider, voice_id,
                        queued_at, started_at, finished_at, interrupted, metadata_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        str(segment.id), str(segment.turn_id), segment.ordinal, segment.display_text, segment.speech_text,
                        segment.status.value, segment.tts_provider, segment.voice_id, segment.queued_at.isoformat(),
                        segment.started_at.isoformat() if segment.started_at else None,
                        segment.finished_at.isoformat() if segment.finished_at else None, int(segment.interrupted),
                        json.dumps(segment.metadata, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if not is_duplicate_error(exc):
                    raise
                return False
        return True

    def list_segments(self, turn_id: UUID | str) -> list[SpeechSegment]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM speech_segments WHERE turn_id = ? ORDER BY ordinal", (str(turn_id),)
            ).fetchall()
        return [self._segment_from_row(row) for row in rows]

    def set_segment_status(self, segment_id: UUID | str, status: MessageStatus, *, interrupted: bool = False) -> bool:
        now = self.clock.now().isoformat()
        started = now if status == MessageStatus.SPEAKING else None
        finished = now if status in {MessageStatus.SPOKEN, MessageStatus.INTERRUPTED, MessageStatus.FAILED} else None
        with self._lock:
            cur = execute_atomic_statement(
                self.backend,
                self._conn,
                """UPDATE speech_segments SET status=?, interrupted=?,
                   started_at=COALESCE(started_at, ?), finished_at=COALESCE(finished_at, ?)
                   WHERE id=?""",
                (status.value, int(interrupted), started, finished, str(segment_id)),
            )
            return cur.rowcount > 0

    def record_usage(
        self,
        *,
        provider: str,
        model: str,
        task_class: str,
        success: bool,
        turn_id: UUID | str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        latency_ms: float | None = None,
        status_code: int | None = None,
        remaining_requests: int | None = None,
        remaining_tokens: int | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        # ADR-0020 keeps the usage ledger as a special fail-closed transaction
        # boundary, including explicit COMMIT ambiguity and recovery semantics.
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO model_usage(timestamp, provider, model, task_class, success, input_tokens, output_tokens,
                   latency_ms, status_code, rate_limit_remaining_requests, rate_limit_remaining_tokens, turn_id, metadata_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    self.clock.now().isoformat(), provider, model, task_class, int(success), input_tokens, output_tokens,
                    latency_ms, status_code, remaining_requests, remaining_tokens, str(turn_id) if turn_id else None,
                    json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
                ),
            )

    def message_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM conversation_messages").fetchone()
            return int(row[0]) if row else 0

    def journal_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM cognitive_journal").fetchone()
            return int(row[0]) if row else 0

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> ConversationMessage:
        return ConversationMessage(
            id=row["id"], turn_id=row["turn_id"], speaker_id=row["speaker_id"], addressee_id=row["addressee_id"],
            source=MessageSource(row["source"]), raw_text=row["raw_text"], normalized_text=row["normalized_text"],
            spoken_text=row["spoken_text"], privacy_scope=PrivacyScope(row["privacy_scope"]),
            remote_processing_allowed=bool(row["remote_processing_allowed"]), stt_confidence=row["stt_confidence"],
            provider=row["provider"], model=row["model"], status=MessageStatus(row["status"]),
            interrupted=bool(row["interrupted"]), source_event_id=row["source_event_id"], session_id=row["session_id"],
            started_at=row["started_at"], finished_at=row["finished_at"], metadata=json.loads(row["metadata_json"]),
            dedupe_key=row["dedupe_key"],
        )

    @staticmethod
    def _journal_from_row(row: sqlite3.Row) -> CognitiveJournalEntry:
        return CognitiveJournalEntry(
            id=row["id"], turn_id=row["turn_id"], stage=row["stage"], timestamp=row["timestamp"], source=row["source"],
            summary=row["summary"], reason_codes=json.loads(row["reason_codes_json"]), payload=json.loads(row["payload_json"]),
            privacy_scope=PrivacyScope(row["privacy_scope"]), provider=row["provider"], model=row["model"],
            duration_ms=row["duration_ms"], correlation_id=row["correlation_id"], error_code=row["error_code"],
            metadata=json.loads(row["metadata_json"]), dedupe_key=row["dedupe_key"],
        )

    @staticmethod
    def _segment_from_row(row: sqlite3.Row) -> SpeechSegment:
        return SpeechSegment(
            id=row["id"], turn_id=row["turn_id"], ordinal=row["ordinal"], display_text=row["display_text"],
            speech_text=row["speech_text"], status=MessageStatus(row["status"]), tts_provider=row["tts_provider"],
            voice_id=row["voice_id"], queued_at=row["queued_at"], started_at=row["started_at"],
            finished_at=row["finished_at"], interrupted=bool(row["interrupted"]), metadata=json.loads(row["metadata_json"]),
        )

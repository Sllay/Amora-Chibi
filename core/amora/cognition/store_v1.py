from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from .models import MessageStatus
from .store import CognitiveStore as BaseCognitiveStore


class CognitiveStore(BaseCognitiveStore):
    """V1 observability and delivery helpers over persistent cognition state."""

    def latest_usage(self, provider: str, *, task_class: str | None = None) -> dict[str, Any] | None:
        query = "SELECT * FROM model_usage WHERE provider = ?"
        args: list[object] = [provider]
        if task_class is not None:
            query += " AND task_class = ?"
            args.append(task_class)
        query += " ORDER BY timestamp DESC, id DESC LIMIT 1"
        with self._lock:
            row = self._conn.execute(query, tuple(args)).fetchone()
        return dict(row) if row is not None else None

    def usage_ledger(self) -> list[dict[str, Any]]:
        """Return compact lifetime usage totals plus the latest quota signals."""
        with self._lock:
            providers = self._conn.execute(
                """SELECT provider, COUNT(*) AS request_count,
                          SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count,
                          COALESCE(SUM(input_tokens), 0) AS input_tokens,
                          COALESCE(SUM(output_tokens), 0) AS output_tokens,
                          MAX(timestamp) AS last_used_at
                   FROM model_usage GROUP BY provider ORDER BY provider"""
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in providers:
            latest = self.latest_usage(str(row["provider"])) or {}
            result.append(
                {
                    "provider": row["provider"],
                    "request_count": int(row["request_count"] or 0),
                    "success_count": int(row["success_count"] or 0),
                    "input_tokens": int(row["input_tokens"] or 0),
                    "output_tokens": int(row["output_tokens"] or 0),
                    "last_used_at": row["last_used_at"],
                    "last_status_code": latest.get("status_code"),
                    "remaining_requests": latest.get("rate_limit_remaining_requests"),
                    "remaining_tokens": latest.get("rate_limit_remaining_tokens"),
                }
            )
        return result

    def record_interrupted_progress(
        self,
        segment_id: UUID | str,
        *,
        spoken_chars: int | None,
    ) -> str | None:
        """Record the known spoken prefix, or explicitly mark it unknown.

        Android local TTS can report character ranges. Streaming PCM providers do
        not currently provide reliable text/audio alignment, so ``None`` means
        "interrupted, exact spoken prefix unknown" rather than guessing.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT speech_text, metadata_json FROM speech_segments WHERE id = ?",
                (str(segment_id),),
            ).fetchone()
        if row is None:
            return None
        metadata = json.loads(row["metadata_json"])
        text = str(row["speech_text"] or "")
        message_id = metadata.get("message_id")
        spoken_text: str | None = None
        if spoken_chars is None:
            metadata["spoken_progress_known"] = False
            metadata["spoken_chars"] = None
        else:
            count = max(0, min(len(text), int(spoken_chars)))
            spoken_text = text[:count]
            metadata["spoken_progress_known"] = True
            metadata["spoken_chars"] = count

        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE speech_segments SET metadata_json = ? WHERE id = ?",
                (json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), str(segment_id)),
            )
        if message_id:
            self.update_message_delivery(
                str(message_id),
                status=MessageStatus.INTERRUPTED,
                spoken_text=spoken_text,
                interrupted=True,
            )
        return spoken_text

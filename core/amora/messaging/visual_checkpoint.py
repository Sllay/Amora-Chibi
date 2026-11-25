"""Private, bounded visual checkpoints, not canonical memories.

The generation lease serializes a turn. The exact cache key additionally
binds the session, source revision, attachment, preview, URL and full focus.
Only validated successful observations are retained; raw inputs never are.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from amora.cognition.visual_context import visual_context_for_request
from amora.events.models import AmoraEvent, EventPriority, PrivacyScope
from amora.latency_trace import trace_lock

EVENT_TYPE = "messaging.visual.checkpoint.v1"
SOURCE = "owner_dm_visual_checkpoint"
TTL_SECONDS = 900


def checkpoint_key(session, turn, native_hash, revision, image, text):
    # Length-delimited fields avoid ambiguous concatenation. Include the exact
    # signed URL fingerprint when a provider might have read the original.
    digest = hashlib.sha256()
    for value in (EVENT_TYPE, session, str(turn), native_hash, str(revision),
                  image.attachment_id, image.content_mime.lower(),
                  image.content_base64, image.source_url or "", text):
        data = value.encode("utf-8")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


class VisualCheckpointStore:
    def __init__(self, database, clock):
        self.database = database
        self.clock = clock

    @staticmethod
    def _dedupe(key, bucket):
        return f"visual-checkpoint:v1:{key}:{bucket}"

    def source_current(self, session, native_hash, revision):
        with trace_lock(self.database._lock):
            row = self.database._conn.execute(
                "SELECT 1 FROM memory_reflection_invalidations WHERE session_id=? "
                "AND native_message_hash=? AND invalidated_through>=?",
                (session, native_hash, revision),
            ).fetchone()
        return row is None

    def load(self, session, native_hash, revision, key):
        if not self.source_current(session, native_hash, revision):
            return None
        now = self.clock.now()
        bucket = int(now.timestamp()) // TTL_SECONDS
        with trace_lock(self.database._lock):
            rows = self.database._conn.execute(
                "SELECT * FROM events WHERE dedupe_key IN (?,?) AND event_type=? "
                "AND source=? AND session_id=? AND privacy_scope='PERSONAL' "
                "AND actor='PERSON_BK' ORDER BY timestamp DESC LIMIT 2",
                (self._dedupe(key, bucket), self._dedupe(key, bucket-1),
                 EVENT_TYPE, SOURCE, session),
            ).fetchall()
        for row in rows:
            try:
                event = self.database._decode_stored_event(row)
                age = now - event.timestamp
                if not timedelta(0) <= age <= timedelta(seconds=TTL_SECONDS):
                    continue
                data = event.payload
                if data.get("key") != key or data.get("revision") != revision:
                    continue
                obs = visual_context_for_request(data.get("observation"),
                    remote=True, private=True, mode="reply")
                if obs and obs.get("status") == "analyzed" and obs.get("source") == "vision_model":
                    return obs
            except (ValueError, TypeError, KeyError, OverflowError):
                continue
        return None

    def save(self, session, native_hash, revision, key, observation):
        if not self.source_current(session, native_hash, revision):
            return False
        obs = visual_context_for_request(observation, remote=True, private=True, mode="reply")
        if not obs or obs.get("status") != "analyzed" or obs.get("source") != "vision_model":
            return False
        now = self.clock.now()
        bucket = int(now.timestamp()) // TTL_SECONDS
        return self.database.append_event(AmoraEvent(
            event_type=EVENT_TYPE, timestamp=now, source=SOURCE,
            actor="PERSON_BK", privacy_scope=PrivacyScope.PERSONAL,
            priority=EventPriority.BACKGROUND, importance=0.0,
            session_id=session, dedupe_key=self._dedupe(key, bucket),
            payload={"key":key, "revision":revision, "observation":obs},
        ))


def contains_transport_input(observation, image):
    # Defense against a malformed provider echo. Ordinary OCR remains data.
    serialized = json.dumps(observation, ensure_ascii=False)
    return ("data:image/" in serialized.lower()
            or image.content_base64 in serialized
            or bool(image.source_url and image.source_url in serialized))

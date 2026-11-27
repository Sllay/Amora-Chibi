"""Private, bounded visual checkpoints, not canonical memories.

The generation lease serializes a turn. The exact cache key additionally
binds the session, source revision, attachment, preview, URL and full focus.
Only validated successful observations are retained; raw inputs never are.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

from amora.cognition.visual_context import visual_context_for_request
from amora.events.models import AmoraEvent, EventPriority, PrivacyScope
from amora.latency_trace import trace_lock

EVENT_TYPE = "messaging.visual.checkpoint.v1"
SOURCE = "owner_dm_visual_checkpoint"
TTL_SECONDS = 900
HISTORY_EVENT = "messaging.visual.reference.v1"
HISTORY_TTL_SECONDS = 86400
HISTORY_MAX_RECORDS = 24


def checkpoint_key(session, turn, native_hash, revision, image, text):
    # Length-delimited fields avoid ambiguous concatenation. Include the exact
    # signed URL fingerprint when a provider might have read the original.
    digest = hashlib.sha256()
    for value in (EVENT_TYPE, "spatial-contract-v1", session, str(turn), native_hash, str(revision),
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


    def save_reference(self, session, native_hash, revision, sent_at, observation,
                       manifest=None,pending=False):
        """Save one completed image-message snapshot, including negative results."""
        if not self.source_current(session, native_hash, revision):
            return False
        obs = visual_context_for_request(observation, remote=True, private=True, mode="reply")
        if not obs or obs.get("observation_version") == 4:
            return False
        now = self.clock.now()
        if not timedelta(0) <= now - sent_at <= timedelta(seconds=HISTORY_TTL_SECONDS):
            return False
        from .visual_evidence import validated_manifest
        if manifest is not None:
            manifest=validated_manifest(manifest)
            if (not manifest or manifest["message_hash"]!=native_hash or manifest["revision"]!=revision
                    or datetime.fromisoformat(manifest["sent_at"])!=sent_at):
                return False
        payload = {"native_hash":native_hash,"revision":revision,
                   "sent_at":sent_at.isoformat(),"observation":obs,
                   **({"manifest":manifest} if manifest is not None else {}),
                   **({"pending":True} if pending else {})}
        key = hashlib.sha256(json.dumps([session,payload],sort_keys=True,
                    ensure_ascii=False,separators=(",",":")).encode()).hexdigest()
        return self.database.append_event(AmoraEvent(
            event_type=HISTORY_EVENT, timestamp=now, source=SOURCE,
            actor="PERSON_BK", privacy_scope=PrivacyScope.PERSONAL,
            priority=EventPriority.BACKGROUND, importance=0.0,
            session_id=session, dedupe_key="visual-reference:v1:"+key,
            payload=payload,
        ))

    def mark_reference_disputed(self, session, native_hash, revision, sent_at,manifest=None):
        """Suppress a disputed sensor assertion, never replace it with user text."""
        if not self.source_current(session,native_hash,revision):
            return False
        now=self.clock.now()
        if not timedelta(0)<=now-sent_at<=timedelta(seconds=HISTORY_TTL_SECONDS):
            return False
        from .incoming_vision import unavailable_visual_observation
        key=hashlib.sha256(json.dumps([session,native_hash,revision,sent_at.isoformat()],
            separators=(",",":")).encode()).hexdigest()
        return self.database.append_event(AmoraEvent(
            event_type=HISTORY_EVENT,timestamp=now,source=SOURCE,
            actor="PERSON_BK",privacy_scope=PrivacyScope.PERSONAL,
            priority=EventPriority.BACKGROUND,importance=0.0,session_id=session,
            dedupe_key="visual-dispute:v1:"+key,
            payload={"native_hash":native_hash,"revision":revision,"sent_at":sent_at.isoformat(),
                     "disputed":True,"observation":unavailable_visual_observation("invalid_observation"),
                     **({"manifest":manifest} if manifest is not None else {})},
        ))

    def load_reference(self, session, before, target_hash=None, exclude_hash=None, selection="latest"):
        """Select the latest registered image message or exact native reply.

        Never fall through from an invalidated/failed latest image to an older
        unrelated image. A bounded history miss is explicitly unavailable.
        """
        now = self.clock.now()
        with trace_lock(self.database._lock):
            rows = self.database._conn.execute(
                "SELECT * FROM events WHERE event_type=? AND session_id=? "
                "AND privacy_scope='PERSONAL' AND actor='PERSON_BK' "
                "ORDER BY timestamp DESC LIMIT ?",
                (HISTORY_EVENT, session, HISTORY_MAX_RECORDS),
            ).fetchall()
        candidates = []
        for row in rows:
            try:
                event = self.database._decode_stored_event(row)
                if event.source != SOURCE:
                    continue
                data = event.payload
                native_hash = data["native_hash"]
                revision = data["revision"]
                sent_at = datetime.fromisoformat(data["sent_at"])
                if (not isinstance(native_hash,str) or len(native_hash)!=20
                        or type(revision) is not int or revision < 1
                        or native_hash == exclude_hash
                        or (target_hash is not None and native_hash != target_hash)
                        or not sent_at < before
                        or not timedelta(0) <= now-sent_at <= timedelta(seconds=HISTORY_TTL_SECONDS)
                        or not timedelta(0) <= now-event.timestamp <= timedelta(seconds=HISTORY_TTL_SECONDS)):
                    continue
                candidates.append((sent_at, revision, event.timestamp, native_hash, data))
            except (ValueError, TypeError, KeyError, OverflowError):
                continue
        if not candidates or selection not in {"latest","first"}:
            return None
        # Earliest in the bounded retained history, not "first ever".
        if selection=="first" and len(rows)>=HISTORY_MAX_RECORDS:
            return None
        revisions={}
        for item in candidates:
            revisions[item[3]]=max(revisions.get(item[3],0),item[1])
        candidates=[item for item in candidates if item[1]==revisions[item[3]]]
        selected=(min if selection=="first" else max)(item[0] for item in candidates)
        tied = [item for item in candidates if item[0] == selected]
        if len({item[3] for item in tied}) != 1:
            return None
        latest_revision=max(item[1] for item in tied)
        same_revision=[item for item in tied if item[1]==latest_revision]
        disputed=[item for item in same_revision if item[4].get("disputed") is True]
        eligible=disputed or [item for item in same_revision if not item[4].get("pending")] or same_revision
        chosen = max(eligible,key=lambda item:item[2])
        if any(item[1:3] == chosen[1:3] and item[4] != chosen[4] for item in eligible):
            return None
        sent_at, revision, observed_at, native_hash, data = chosen
        if not self.source_current(session,native_hash,revision):
            return None
        obs = visual_context_for_request(data.get("observation"),remote=True,private=True,mode="reply")
        if not obs or obs.get("observation_version")==4:
            return None
        from .visual_evidence import validated_manifest,reference_trace
        manifest=validated_manifest(data.get("manifest"))
        if data.get("manifest") is not None and (not manifest or manifest["message_hash"]!=native_hash
                or manifest["revision"]!=revision or datetime.fromisoformat(manifest["sent_at"])!=sent_at):
            return None
        reference_trace("selected",session,native_hash,revision,
                        "pending" if data.get("pending") else "disputed" if data.get("disputed") else obs["status"])
        return {"native_hash":native_hash,"revision":revision,
                "observed_at":observed_at.isoformat(),"sent_at":sent_at.isoformat(),"observation":obs,
                **({"manifest":manifest} if manifest else {}),
                **({"pending":True} if data.get("pending") else {}),
                **({"disputed":True} if data.get("disputed") is True else {})}


def contains_transport_input(observation, image):
    # Defense against a malformed provider echo. Ordinary OCR remains data.
    import re
    serialized = json.dumps(observation, ensure_ascii=False)
    lowered=serialized.lower()
    return ("data:image/" in lowered
            or image.content_base64 in serialized
            or bool(image.source_url and image.source_url in serialized)
            or bool(re.search(r"https?://(?:cdn\.discordapp\.com|media\.discordapp\.net)/attachments/",lowered))
            or bool(re.search(r"[A-Za-z0-9+/]{256,}={0,2}",serialized)))

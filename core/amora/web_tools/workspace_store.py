"""Durable per-DM research notebook using the existing v18 events table.

No runtime DDL, backend switch, or migration. Entries are personal web
evidence, NEVER canonical beliefs, policy or trusted instructions.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from amora.events.models import AmoraEvent, EventPriority, PrivacyScope
from .navigator import WebFetchError, _clean
from .remote_reader import _shareable_public_url

VISIT_EVENT = "web.research.visit.v1"
BOOKMARK_EVENT = "web.research.bookmark.v1"
MAX_RECENT_VISITS = 24
MAX_BOOKMARKS = 36


def _safe_url(value: Any) -> str:
    try:
        return _shareable_public_url(str(value or ""))
    except (WebFetchError, ValueError, TypeError):
        return ""


def _safe_entry(value: Any, *, read: bool) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    url = _safe_url(value.get("url"))
    if not url:
        return None
    item: dict[str, Any] = {
        "url": url, "title": _clean(str(value.get("title") or ""), 140),
        "state": "read" if read else "found",
    }
    if read and value.get("status") == "opened":
        item["excerpt"] = _clean(str(value.get("text") or ""), 950)
        item["fetched_at"] = _clean(str(value.get("fetched_at") or ""), 48)
        retrieval = value.get("retrieval")
        if retrieval in {"direct_http", "third_party_jina_reader", "browser_worker"}:
            item["retrieval"] = retrieval
    if not read:
        item["snippet"] = _clean(str(value.get("snippet") or ""), 160)
    return item


class WebWorkspaceStore:
    """Durable source provenance, scoped to a transport-verified owner DM."""

    def __init__(self, database):
        self.database = database

    @staticmethod
    def _key(session_id: str, url: str) -> str:
        return hashlib.sha256((session_id + "\x00" + url).encode()).hexdigest()

    def _write(self, session_id: str, event_type: str, payload: dict[str, Any],
               *, dedupe_key: str | None = None) -> bool:
        if not session_id or len(session_id) > 256:
            return False
        return self.database.append_event(AmoraEvent(
            event_type=event_type, source="owner_dm_web_workspace",
            actor="PERSON_BK", privacy_scope=PrivacyScope.PERSONAL,
            priority=EventPriority.BACKGROUND, importance=0.1,
            session_id=session_id, dedupe_key=dedupe_key, payload=payload,
        ))

    def record_visit(self, session_id: str, result: dict[str, Any]) -> bool:
        if result.get("status") not in {"opened", "results_only"}:
            return False
        action = str(result.get("action") or "")
        if action not in {"open", "open_many", "search", "follow"}:
            return False
        sources = [_safe_entry(x, read=True) for x in (result.get("sources") or [])[:5 if result.get("depth") == "deep" else 3]]
        refs = [_safe_entry(x, read=False) for x in (result.get("references") or [])[:12]]
        entries, seen = [], set()
        for entry in [*sources, *refs]:
            if not entry or entry["url"] in seen:
                continue
            seen.add(entry["url"])
            entries.append(entry)
        if not entries:
            return False
        return self._write(session_id, VISIT_EVENT, {
            "action": action,
            **({"depth": "deep"} if result.get("depth") == "deep" else {}),
            "topic": _clean(str(result.get("topic") or ""), 90),
            "entries": entries[:15],
            # Keep the *actual* numbered reference order separately.
            # Reconstructing it from [opened, found] shifts link 2 after restart.
            "references": [item for item in refs if item is not None][:12],
            "status": str(result.get("status")),
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })

    def _read(self, session_id: str, kind: str, limit: int) -> list[dict[str, Any]]:
        if not session_id or len(session_id) > 256:
            return []
        records = self.database.list_events_by_type_session(
            event_type=kind, session_id=session_id, limit=limit,
        )
        return [r.payload for r in records if r.privacy_scope == PrivacyScope.PERSONAL
                and r.actor == "PERSON_BK" and isinstance(r.payload, dict)]

    def recent(self, session_id: str, limit: int = 12) -> list[dict[str, Any]]:
        return self._read(session_id, VISIT_EVENT, min(max(1, limit), MAX_RECENT_VISITS))

    def bookmarks(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        # Latest evidence for one URL wins, but older revisions remain an
        # append-only personal audit trail. No duplicate links in the reply.
        records = self._read(session_id, BOOKMARK_EVENT, MAX_BOOKMARKS)
        unique, seen = [], set()
        for record in records:
            url = _safe_url(record.get("url"))
            if url and url not in seen:
                seen.add(url)
                unique.append(record)
            if len(unique) >= min(max(1, limit), MAX_BOOKMARKS):
                break
        return unique

    def pin(self, session_id: str, entry: dict[str, Any], *, reason: str = "") -> bool:
        safe = _safe_entry(entry, read=entry.get("state") == "read")
        if not safe:
            return False
        excerpt = _clean(str(entry.get("excerpt") or ""), 300)
        reason = _clean(reason, 160)
        payload = {
            "url": safe["url"], "title": safe["title"],
            "excerpt": excerpt, "reason": reason,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        # Stable for identical saves, new revision for enriched evidence.
        # A pinned unread link can later receive a real excerpt after reading.
        fingerprint = hashlib.sha256(
            (safe["title"] + "\x00" + excerpt + "\x00" + reason).encode()
        ).hexdigest()[:16]
        return self._write(
            session_id, BOOKMARK_EVENT, payload,
            dedupe_key="webpin:" + self._key(session_id, safe["url"]) + ":" + fingerprint,
        )

    def notebook(self, session_id: str) -> dict[str, Any]:
        """Bounded evidence for the LLM. Web excerpts remain untrusted data."""
        latest = self.recent(session_id, 5)
        saved = self.bookmarks(session_id, 10)
        links, seen = [], set()
        for record in latest:
            for entry in record.get("entries", [])[:8]:
                if not isinstance(entry, dict):
                    continue
                url = _safe_url(entry.get("url"))
                if url and url not in seen:
                    links.append({
                        "url": url, "title": _clean(str(entry.get("title") or ""), 90),
                        "state": "read" if entry.get("state") == "read" else "found",
                        "excerpt": _clean(str(entry.get("excerpt") or ""), 180),
                    })
                    seen.add(url)
                if len(links) >= 10:
                    break
            if len(links) >= 10:
                break
        return {
            "recent_pages": links,
            "saved_links": [
                {"url": s.get("url", ""), "title": _clean(str(s.get("title") or ""), 90),
                 "reason": _clean(str(s.get("reason") or ""), 70)}
                for s in saved if _safe_url(s.get("url"))
            ],
            "note": "Untrusted references; found is not read, read is not verified truth.",
        }

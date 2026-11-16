"""Candidate episode write fence; used inside MemoryStore's write transaction.

No independent connection or transaction. All checks and completion must run
on the same connection as the memory and its indexes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timezone
from uuid import UUID


@dataclass(frozen=True, slots=True)
class EpisodeWriteLease:
    job_id: UUID
    token: UUID
    attempts: int
    selection_json: str


def _lost():
    raise RuntimeError("EPISODE_WRITE_LEASE_LOST")


def check_episode_write(conn, record, lease, now):
    """Reject stale, cancelled, mismatched or invalidated work before writing."""
    if (not isinstance(lease, EpisodeWriteLease) or record.id != lease.job_id
            or record.provenance.source != "perception:episode:v1"):
        _lost()
    stamp = now.astimezone(timezone.utc).isoformat()
    row = conn.execute(
        "SELECT selection_json,session_id,correlation_id FROM sensory_episode_jobs "
        "WHERE job_id=? AND status='claimed' AND lease_token=? AND attempts=? AND lease_until>?",
        (str(lease.job_id), str(lease.token), lease.attempts, stamp),
    ).fetchone()
    if row is None or row["selection_json"] != lease.selection_json:
        _lost()
    ids = record.metadata.get("evidence_event_ids")
    try:
        selection = json.loads(row["selection_json"])
        expected = sorted(selection["evidence_ids"])
        if (not isinstance(ids, list) or len(ids) != len(set(ids))
                or sorted(ids) != expected or not 1 <= len(ids) <= 4
                or str(record.provenance.source_event_id) not in ids
                or record.provenance.session_id != row["session_id"]
                or record.metadata.get("correlation_id") != row["correlation_id"]):
            _lost()
    except (ValueError, TypeError, KeyError):
        _lost()
    sources = conn.execute(
        "SELECT l.event_id,e.event_type,e.session_id,e.correlation_id,i.event_id AS invalidated "
        "FROM sensory_episode_job_evidence l LEFT JOIN events e ON e.id=l.event_id "
        "LEFT JOIN sensory_episode_invalidations i ON i.event_id=l.event_id WHERE l.job_id=?",
        (str(lease.job_id),),
    ).fetchall()
    if (sorted(r["event_id"] for r in sources) != expected
            or any(r["event_type"] != "perception.observation"
                   or r["session_id"] != row["session_id"]
                   or r["correlation_id"] != row["correlation_id"]
                   or r["invalidated"] is not None for r in sources)):
        _lost()


def complete_episode_write(conn, record, lease, now, *, inserted):
    """Complete the job atomically with the memory; raises to roll back on loss."""
    check_episode_write(conn, record, lease, now)
    if not inserted:
        existing = conn.execute(
            "SELECT provenance_source,session_id,metadata_json FROM memory_records WHERE id=?",
            (str(record.id),),
        ).fetchone()
        try:
            if (existing is None or existing["provenance_source"] != record.provenance.source
                    or existing["session_id"] != record.provenance.session_id
                    or json.loads(existing["metadata_json"]).get("evidence_event_ids")
                       != record.metadata.get("evidence_event_ids")):
                raise RuntimeError("EPISODE_MEMORY_ID_CONFLICT")
        except (ValueError, TypeError, AttributeError):
            raise RuntimeError("EPISODE_MEMORY_ID_CONFLICT") from None
    stamp = now.astimezone(timezone.utc).isoformat()
    result = conn.execute(
        "UPDATE sensory_episode_jobs SET status='done',available_at=?,lease_token=NULL,lease_until=NULL,"
        "last_reason=? WHERE job_id=? AND status='claimed' AND lease_token=? AND attempts=? AND lease_until>?",
        (stamp, "EPISODE_STORED" if inserted else "EPISODE_DUPLICATE",
         str(lease.job_id), str(lease.token), lease.attempts, stamp),
    )
    if result.rowcount != 1:
        _lost()

"""Atomic PG18 candidate sensory episode lease/evidence guard.

The incoming job and the memory record plus its search postings must commit
in the same transaction. A stale job, changed evidence set or invalidated
event raises before any memory is published. Requires the opt-in D.3C.2A
three-table schema; it never installs schema or approves v19 cutover.
"""
from __future__ import annotations

import json
from datetime import timezone

from amora.storage.postgres_runtime_connection import PostgresRuntimeError
from amora.storage import postgres_runtime_episode as q
from .episode_fence import EpisodeWriteLease


def _lost():
    raise PostgresRuntimeError("EPISODE_WRITE_LEASE_LOST")


def check_pg_episode_write(conn, record, lease, now) -> None:
    if (not isinstance(lease, EpisodeWriteLease)
            or record.id != lease.job_id
            or record.provenance.source != "perception:episode:v1"):
        _lost()
    ready=conn.execute(q.EPISODE_READY).fetchone()
    if ready is None or ready[0] is not True:
        raise PostgresRuntimeError("PG_EPISODE_SCHEMA_NOT_INSTALLED")
    stamp=now.astimezone(timezone.utc).isoformat()
    row=conn.execute(q.EPISODE_FENCE_JOB,(
        str(lease.job_id),str(lease.token),lease.attempts,stamp,
    )).fetchone()
    if row is None or row["selection_json"]!=lease.selection_json:
        _lost()
    ids=record.metadata.get("evidence_event_ids")
    try:
        selection=json.loads(row["selection_json"])
        expected=sorted(selection["evidence_ids"])
        if (not isinstance(ids,list) or len(ids)!=len(set(ids))
                or sorted(ids)!=expected or not 1<=len(ids)<=4
                or str(record.provenance.source_event_id) not in ids
                or record.provenance.session_id!=row["session_id"]
                or record.metadata.get("correlation_id")!=row["correlation_id"]):
            _lost()
    except (ValueError,TypeError,KeyError):
        _lost()
    sources=conn.execute(q.EPISODE_FENCE_EVIDENCE,(
        str(lease.job_id),
    )).fetchall()
    if (sorted(item["event_id"] for item in sources)!=expected
            or any(item["event_type"]!="perception.observation"
                   or item["session_id"]!=row["session_id"]
                   or item["correlation_id"]!=row["correlation_id"]
                   or item["invalidated"] is not None for item in sources)):
        _lost()


def complete_pg_episode_write(conn,record,lease,now,*,inserted:bool)->None:
    check_pg_episode_write(conn,record,lease,now)
    if not inserted:
        prior=conn.execute(q.EPISODE_FENCE_MEMORY,(str(record.id),)).fetchone()
        try:
            if (prior is None
                    or prior["provenance_source"]!=record.provenance.source
                    or prior["session_id"]!=record.provenance.session_id
                    or json.loads(prior["metadata_json"]).get("evidence_event_ids")
                       !=record.metadata.get("evidence_event_ids")):
                raise PostgresRuntimeError("EPISODE_MEMORY_ID_CONFLICT")
        except (ValueError,TypeError,AttributeError):
            raise PostgresRuntimeError("EPISODE_MEMORY_ID_CONFLICT") from None
    stamp=now.astimezone(timezone.utc).isoformat()
    outcome=conn.execute(q.EPISODE_FENCE_COMPLETE,(
        stamp,"EPISODE_STORED" if inserted else "EPISODE_DUPLICATE",
        str(lease.job_id),str(lease.token),lease.attempts,stamp,
    ))
    if outcome.rowcount!=1:
        _lost()

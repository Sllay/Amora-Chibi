"""Version-neutral *read-model* projection for optional Altare PG17.

This is a pure serializer + authorization/freshness policy, NOT a database
replicator, a second source of truth, or a new production connection.
The only durable writer remains the approved Aiven PG18 Core backend.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any

from amora.events import PrivacyScope
from amora.memory.models import MemoryRecord

PROJECTION_FORMAT = 1
MAX_SUMMARY_LENGTH = 900
MAX_STALENESS = timedelta(seconds=30)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("READMODEL_TIMESTAMP_MUST_BE_TZ_AWARE")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class MemoryReadProjection:
    memory_id: str
    owner_entity_id: str
    kind: str
    summary: str
    privacy_scope: str
    origin_updated_at: datetime
    origin_sequence: int
    materialized_at: datetime
    checksum: str
    valid_until: datetime | None
    archived: bool
    schema: int = PROJECTION_FORMAT


def project_memory(
    record: MemoryRecord,
    *, origin_sequence: int,
    materialized_at: datetime,
) -> MemoryReadProjection | None:
    """Project a reviewed memory without pretending it is a full DB clone.

    Privacy boundaries:
      * no SECRET/AUTH records;
      * personal items require an explicit owner;
      * no items marked as forbidden to process remotely;
      * archived items are represented as tombstones (no content);
      * sequence must come from the committed authoritative changelog.
    """
    if type(origin_sequence) is not int or origin_sequence <= 0:
        raise ValueError("READMODEL_SEQUENCE_REQUIRED")
    if record.privacy_scope != PrivacyScope.PERSONAL:
        return None
    if not record.owner_entity_id or not record.remote_processing_allowed:
        return None
    updated = _utc(record.updated_at)
    materialized = _utc(materialized_at)
    if materialized < updated:
        raise ValueError("READMODEL_TIMESTAMP_BEFORE_COMMIT")
    deleted = record.archived_at is not None
    summary = "" if deleted else (record.summary or record.content)[:MAX_SUMMARY_LENGTH]
    fingerprint = sha256(
        f"{record.id}\x00{record.owner_entity_id}\x00{origin_sequence}\x00"
        f"{updated.isoformat()}\x00{summary}\x00{deleted}".encode()
    ).hexdigest()
    return MemoryReadProjection(
        memory_id=str(record.id),
        owner_entity_id=record.owner_entity_id,
        kind=record.kind.value,
        summary=summary,
        privacy_scope=record.privacy_scope.value,
        origin_updated_at=updated,
        origin_sequence=origin_sequence,
        materialized_at=materialized,
        checksum=fingerprint,
        valid_until=_utc(record.valid_until) if record.valid_until else None,
        archived=deleted,
    )


def can_serve_projection(
    item: MemoryReadProjection,
    *, requester_owner_id: str,
    required_sequence: int,
    now: datetime,
    max_staleness: timedelta = MAX_STALENESS,
) -> bool:
    """Never return a known-stale/private projection as current memory.

    required_sequence is the latest known canonical revision for this record,
    not the local cache's own sequence. When this watermark is UNKNOWN, the
    caller must use Aiven. Critical/sensitive queries always use Aiven.
    """
    current = _utc(now)
    if (not requester_owner_id or item.owner_entity_id != requester_owner_id
            or item.privacy_scope != PrivacyScope.PERSONAL.value
            or item.schema != PROJECTION_FORMAT or item.archived):
        return False
    if type(required_sequence) is not int or required_sequence <= 0:
        return False
    if item.origin_sequence < required_sequence:
        return False
    if max_staleness <= timedelta() or max_staleness > MAX_STALENESS:
        return False
    if current < item.materialized_at:
        return False
    if current - item.materialized_at > max_staleness:
        return False
    if item.valid_until is not None and current > item.valid_until:
        return False
    return bool(item.checksum and item.summary)

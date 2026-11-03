from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    AttentionDecision,
    ExpressionRef,
    ExpressionSemanticProfile,
    InboundMessage,
    MessagingPlatform,
    RoutingAction,
)


class ExpressionSemanticStatus(StrEnum):
    METADATA_ONLY = "metadata_only"
    PENDING_VISUAL = "pending_visual"
    ENRICHED = "enriched"
    STALE = "stale"
    FAILED = "failed"


class PerceptualExpressionRecord(BaseModel):
    """Persistent 'CPF' for one transport-native expression in one social scope."""

    model_config = ConfigDict(extra="forbid")

    platform: MessagingPlatform
    account_scope: str
    scope_id: str
    kind: str
    native_id: str
    name: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    semantic_profile: ExpressionSemanticProfile | None = None
    semantic_status: ExpressionSemanticStatus
    fingerprint: str
    fingerprint_source: str
    content_fingerprint: str | None = None
    content_mime: str | None = None
    content_size_bytes: int | None = None
    semantic_revision: int = 0
    first_seen_at: datetime
    last_seen_at: datetime
    seen_count: int = 0
    unique_senders_count: int = 0
    last_sender_ref: str | None = None
    last_source_message_id: str | None = None
    last_visual_analysis_at: datetime | None = None
    last_visual_analysis_fingerprint: str | None = None
    last_visual_analysis_content_fingerprint: str | None = None
    visual_provider: str | None = None
    visual_model: str | None = None
    visual_claimed_until: datetime | None = None
    visual_attempt_count: int = 0
    visual_last_error_code: str | None = None
    visual_defer_until: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def memory_key(self) -> str:
        return ":".join(
            (
                self.platform.value,
                self.account_scope,
                self.scope_id,
                self.kind,
                self.native_id,
            )
        )

    @property
    def needs_visual_enrichment(self) -> bool:
        if self.semantic_status in {
            ExpressionSemanticStatus.METADATA_ONLY,
            ExpressionSemanticStatus.PENDING_VISUAL,
            ExpressionSemanticStatus.STALE,
            ExpressionSemanticStatus.FAILED,
        }:
            return True
        if self.last_visual_analysis_content_fingerprint is None:
            return True
        return (
            self.content_fingerprint is not None
            and self.content_fingerprint != self.last_visual_analysis_content_fingerprint
        )


class PerceptualExpressionClaim(BaseModel):
    """Opaque lease over one CPF revision pending real visual perception."""

    model_config = ConfigDict(extra="forbid")

    record: PerceptualExpressionRecord
    claim_token: str = Field(min_length=16, max_length=128)
    claimed_until: datetime


class PerceptualExpressionMemory:
    """Persist and reuse expression meaning without scanning an entire platform.

    Availability and knowledge are intentionally separate:
    * the adapter may expose a large ``available_expressions`` catalog;
    * only expressions that actually participate in a relevant Amora interaction
      are promoted into this memory;
    * future catalog entries are hydrated only when a matching CPF already exists.

    Metadata fingerprint and visual content fingerprint are deliberately distinct:
    metadata revisions guard stale transport observations, while content SHA-256
    identifies the bytes actually perceived. Visual work is leased so concurrent
    workers cannot analyze the same CPF revision simultaneously.
    """

    MAX_SCOPE_RECORDS = 1024
    MAX_VISUAL_CLAIMS = 32
    MIN_VISUAL_LEASE_SECONDS = 1
    MAX_VISUAL_LEASE_SECONDS = 900
    MAX_CONTENT_BYTES = 20 * 1024 * 1024

    def __init__(self, messaging_store, clock) -> None:
        self.store = messaging_store
        self.database = messaging_store.database
        self.clock = clock

    @staticmethod
    def scope_id(message: InboundMessage) -> str:
        return str(message.guild_or_group_id or message.conversation_id)

    @staticmethod
    def memory_key(
        *,
        platform: MessagingPlatform,
        account_scope: str,
        scope_id: str,
        kind: str,
        native_id: str,
    ) -> str:
        return ":".join((platform.value, account_scope, scope_id, kind, native_id))

    @staticmethod
    def metadata_fingerprint(expression: ExpressionRef) -> str:
        """Fingerprint metadata only; never claim it is a visual/content hash."""

        payload = {
            "kind": expression.kind.value,
            "name": expression.name or "",
            "description": expression.description or "",
            "tags": sorted({str(item).strip() for item in expression.tags if str(item).strip()}),
            "format_type": expression.format_type,
        }
        canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def hydrate(self, message: InboundMessage) -> InboundMessage:
        """Attach persisted CPF semantics to assets that are currently available."""

        records = self._scope_records(
            platform=message.platform,
            account_scope=message.account_scope,
            scope_id=self.scope_id(message),
        )
        if not records:
            return message

        def hydrate_one(expression: ExpressionRef) -> ExpressionRef:
            record = records.get((expression.kind.value, expression.native_id))
            if record is None:
                return expression.model_copy(
                    update={
                        "known_to_amora": False,
                        "memory_key": None,
                        "semantic_revision": 0,
                        "needs_visual_enrichment": False,
                    }
                )

            current_fingerprint = self.metadata_fingerprint(expression)
            fingerprint_matches = current_fingerprint == record.fingerprint
            semantic_quarantined = (
                not fingerprint_matches
                or record.semantic_status in {
                    ExpressionSemanticStatus.STALE,
                    ExpressionSemanticStatus.FAILED,
                }
            )
            semantic_profile = None if semantic_quarantined else record.semantic_profile
            return expression.model_copy(
                update={
                    "name": expression.name or record.name,
                    "description": expression.description or record.description,
                    "tags": expression.tags or list(record.tags),
                    "semantic_profile": semantic_profile,
                    "known_to_amora": True,
                    "memory_key": record.memory_key,
                    "semantic_revision": record.semantic_revision,
                    "needs_visual_enrichment": semantic_quarantined or record.needs_visual_enrichment,
                    "fingerprint": current_fingerprint if not fingerprint_matches else record.fingerprint,
                    "fingerprint_source": record.fingerprint_source,
                }
            )

        return message.model_copy(
            update={
                "expressions": [hydrate_one(item) for item in message.expressions],
                "available_expressions": [hydrate_one(item) for item in message.available_expressions],
            }
        )

    def observe_relevant(
        self,
        message: InboundMessage,
        decision: AttentionDecision,
    ) -> InboundMessage:
        """Learn only expressions that actually addressed/engaged Amora."""

        if decision.action != RoutingAction.RESPOND or not message.expressions:
            return self.hydrate(message)

        sender_ref = str(decision.speaker_entity_id or decision.speaker_actor_ref)
        source_message_id = message.native_source_message_id
        scope_id = self.scope_id(message)
        for expression in message.expressions:
            self._observe_expression(
                message=message,
                expression=expression,
                scope_id=scope_id,
                sender_ref=sender_ref,
                source_message_id=source_message_id,
            )
        return self.hydrate(message)

    def get(
        self,
        *,
        platform: MessagingPlatform,
        account_scope: str,
        scope_id: str,
        kind: str,
        native_id: str,
    ) -> PerceptualExpressionRecord | None:
        with self.database._lock:
            row = self.database._conn.execute(
                """SELECT * FROM perceptual_expression_assets
                   WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?
                   LIMIT 1""",
                (platform.value, account_scope, scope_id, kind, native_id),
            ).fetchone()
        return self._record_from_row(row) if row is not None else None

    def pending_visual(self, *, limit: int = 32) -> tuple[PerceptualExpressionRecord, ...]:
        limit = max(1, min(256, int(limit)))
        with self.database._lock:
            rows = self.database._conn.execute(
                """SELECT * FROM perceptual_expression_assets
                   WHERE semantic_status IN ('metadata_only','pending_visual','stale','failed')
                      OR last_visual_analysis_content_fingerprint IS NULL
                      OR (
                           content_fingerprint IS NOT NULL
                           AND last_visual_analysis_content_fingerprint <> content_fingerprint
                      )
                   ORDER BY last_seen_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return tuple(self._record_from_row(row) for row in rows)

    def claim_visual(
        self,
        *,
        platform: MessagingPlatform,
        account_scope: str,
        limit: int = 1,
        lease_seconds: int = 120,
    ) -> tuple[PerceptualExpressionClaim, ...]:
        """Lease pending visual work transactionally.

        A lease is scoped to the current metadata fingerprint. Expired leases are
        reclaimable; old claim tokens cannot commit after a newer claim replaces them.
        """

        limit = max(1, min(self.MAX_VISUAL_CLAIMS, int(limit)))
        lease_seconds = max(
            self.MIN_VISUAL_LEASE_SECONDS,
            min(self.MAX_VISUAL_LEASE_SECONDS, int(lease_seconds)),
        )
        now = self.clock.now()
        now_iso = now.isoformat()
        claimed_until = now + timedelta(seconds=lease_seconds)
        claimed_until_iso = claimed_until.isoformat()
        claims: list[PerceptualExpressionClaim] = []

        with self.database._lock, self.database._conn:
            rows = self.database._conn.execute(
                """SELECT * FROM perceptual_expression_assets
                   WHERE platform=? AND account_scope=?
                     AND (
                          semantic_status IN ('metadata_only','pending_visual','stale','failed')
                          OR last_visual_analysis_content_fingerprint IS NULL
                          OR (
                               content_fingerprint IS NOT NULL
                               AND last_visual_analysis_content_fingerprint <> content_fingerprint
                          )
                     )
                     AND (visual_defer_until IS NULL OR visual_defer_until <= ?)
                     AND (
                          visual_claim_token IS NULL
                          OR visual_claimed_until IS NULL
                          OR visual_claimed_until <= ?
                     )
                   ORDER BY last_seen_at DESC
                   LIMIT ?""",
                (platform.value, account_scope, now_iso, now_iso, limit),
            ).fetchall()

            for row in rows:
                token = secrets.token_urlsafe(24)
                key = self._row_key(row)
                cur = self.database._conn.execute(
                    """UPDATE perceptual_expression_assets
                       SET visual_claim_token=?, visual_claimed_until=?,
                           visual_attempt_count=visual_attempt_count+1,
                           visual_last_error_code=NULL
                       WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?
                         AND fingerprint=?
                         AND (visual_defer_until IS NULL OR visual_defer_until <= ?)
                         AND (
                              visual_claim_token IS NULL
                              OR visual_claimed_until IS NULL
                              OR visual_claimed_until <= ?
                         )""",
                    (
                        token,
                        claimed_until_iso,
                        *key,
                        str(row["fingerprint"]),
                        now_iso,
                        now_iso,
                    ),
                )
                if cur.rowcount <= 0:
                    continue
                current = self.database._conn.execute(
                    """SELECT * FROM perceptual_expression_assets
                       WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?""",
                    key,
                ).fetchone()
                if current is None:
                    continue
                claims.append(
                    PerceptualExpressionClaim(
                        record=self._record_from_row(current),
                        claim_token=token,
                        claimed_until=claimed_until,
                    )
                )
        return tuple(claims)

    def record_visual_content(
        self,
        *,
        platform: MessagingPlatform,
        account_scope: str,
        scope_id: str,
        kind: str,
        native_id: str,
        claim_token: str,
        expected_fingerprint: str,
        content_fingerprint: str,
        content_mime: str,
        content_size_bytes: int,
    ) -> bool:
        """Bind fetched bytes to a live claim; return True only when analysis is needed.

        If the exact content SHA was already analyzed, the existing semantic profile
        is revalidated for the current metadata revision without another provider call.
        """

        content_sha = self._normalize_sha256(content_fingerprint)
        mime = self._normalize_mime(content_mime)
        size = self._normalize_content_size(content_size_bytes)
        now = self.clock.now()
        key = (platform.value, account_scope, scope_id, kind, native_id)

        with self.database._lock, self.database._conn:
            row = self.database._conn.execute(
                """SELECT fingerprint, semantic_profile_json, semantic_revision,
                          last_visual_analysis_content_fingerprint,
                          visual_claim_token, visual_claimed_until
                   FROM perceptual_expression_assets
                   WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?""",
                key,
            ).fetchone()
            if row is None or str(row["fingerprint"]) != str(expected_fingerprint):
                return False
            if not self._claim_is_live(
                row,
                claim_token=claim_token,
                now=now,
            ):
                return False

            already_analyzed = (
                bool(row["semantic_profile_json"])
                and int(row["semantic_revision"] or 0) > 0
                and str(row["last_visual_analysis_content_fingerprint"] or "") == content_sha
            )
            if already_analyzed:
                self.database._conn.execute(
                    """UPDATE perceptual_expression_assets
                       SET content_fingerprint=?, content_mime=?, content_size_bytes=?,
                           semantic_status='enriched',
                           last_visual_analysis_fingerprint=?,
                           visual_claim_token=NULL, visual_claimed_until=NULL,
                           visual_last_error_code=NULL, visual_defer_until=NULL
                       WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?
                         AND fingerprint=? AND visual_claim_token=?""",
                    (
                        content_sha,
                        mime,
                        size,
                        expected_fingerprint,
                        *key,
                        expected_fingerprint,
                        claim_token,
                    ),
                )
                return False

            cur = self.database._conn.execute(
                """UPDATE perceptual_expression_assets
                   SET content_fingerprint=?, content_mime=?, content_size_bytes=?
                   WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?
                     AND fingerprint=? AND visual_claim_token=?""",
                (
                    content_sha,
                    mime,
                    size,
                    *key,
                    expected_fingerprint,
                    claim_token,
                ),
            )
            return cur.rowcount > 0

    def apply_visual_enrichment(
        self,
        *,
        platform: MessagingPlatform,
        account_scope: str,
        scope_id: str,
        kind: str,
        native_id: str,
        profile: ExpressionSemanticProfile,
        expected_fingerprint: str,
        claim_token: str,
        content_fingerprint: str,
        provider: str,
        model: str,
        source: str = "visual_perception",
    ) -> bool:
        """Commit structured perception for the exact claimed metadata/content pair.

        A successful retry for already-committed content is idempotent even after the
        lease was cleared, which makes network ambiguity safe for the worker.
        """

        content_sha = self._normalize_sha256(content_fingerprint)
        now = self.clock.now()
        now_iso = now.isoformat()
        trusted_profile = profile.model_copy(update={"source": str(source)[:64] or "visual_perception"})
        payload = json.dumps(
            trusted_profile.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        provider_name = self._bounded_label(provider, 64, "unknown")
        model_name = self._bounded_label(model, 128, "unknown")
        key = (platform.value, account_scope, scope_id, kind, native_id)

        with self.database._lock, self.database._conn:
            row = self.database._conn.execute(
                """SELECT fingerprint, semantic_status, content_fingerprint,
                          last_visual_analysis_content_fingerprint,
                          visual_claim_token, visual_claimed_until
                   FROM perceptual_expression_assets
                   WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?""",
                key,
            ).fetchone()
            if row is None or str(row["fingerprint"]) != str(expected_fingerprint):
                return False

            if (
                str(row["semantic_status"]) == ExpressionSemanticStatus.ENRICHED.value
                and str(row["last_visual_analysis_content_fingerprint"] or "") == content_sha
            ):
                return True

            if not self._claim_is_live(row, claim_token=claim_token, now=now):
                return False
            if str(row["content_fingerprint"] or "") != content_sha:
                return False

            cur = self.database._conn.execute(
                """UPDATE perceptual_expression_assets
                   SET semantic_profile_json=?, semantic_status='enriched',
                       semantic_revision=semantic_revision+1,
                       last_visual_analysis_at=?,
                       last_visual_analysis_fingerprint=?,
                       last_visual_analysis_content_fingerprint=?,
                       visual_provider=?, visual_model=?,
                       visual_claim_token=NULL, visual_claimed_until=NULL,
                       visual_last_error_code=NULL, visual_defer_until=NULL
                   WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?
                     AND fingerprint=? AND content_fingerprint=? AND visual_claim_token=?""",
                (
                    payload,
                    now_iso,
                    expected_fingerprint,
                    content_sha,
                    provider_name,
                    model_name,
                    *key,
                    expected_fingerprint,
                    content_sha,
                    claim_token,
                ),
            )
            return cur.rowcount > 0

    def defer_visual(
        self,
        *,
        platform: MessagingPlatform,
        account_scope: str,
        scope_id: str,
        kind: str,
        native_id: str,
        claim_token: str,
        expected_fingerprint: str,
        error_code: str,
        retry_after_seconds: int = 60,
    ) -> bool:
        """Release a live claim after a bounded failure and schedule a retry window."""

        now = self.clock.now()
        retry_after_seconds = max(1, min(86_400, int(retry_after_seconds)))
        defer_until = now + timedelta(seconds=retry_after_seconds)
        key = (platform.value, account_scope, scope_id, kind, native_id)

        with self.database._lock, self.database._conn:
            row = self.database._conn.execute(
                """SELECT fingerprint, visual_claim_token, visual_claimed_until
                   FROM perceptual_expression_assets
                   WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?""",
                key,
            ).fetchone()
            if row is None or str(row["fingerprint"]) != str(expected_fingerprint):
                return False
            if not self._claim_is_live(row, claim_token=claim_token, now=now):
                return False

            cur = self.database._conn.execute(
                """UPDATE perceptual_expression_assets
                   SET semantic_status='failed',
                       visual_claim_token=NULL, visual_claimed_until=NULL,
                       visual_last_error_code=?, visual_defer_until=?
                   WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?
                     AND fingerprint=? AND visual_claim_token=?""",
                (
                    self._bounded_label(error_code, 96, "VISUAL_ENRICHMENT_FAILED"),
                    defer_until.isoformat(),
                    *key,
                    expected_fingerprint,
                    claim_token,
                ),
            )
            return cur.rowcount > 0

    def known_count(self) -> int:
        with self.database._lock:
            row = self.database._conn.execute("SELECT COUNT(*) FROM perceptual_expression_assets").fetchone()
        return int(row[0]) if row else 0

    def _observe_expression(
        self,
        *,
        message: InboundMessage,
        expression: ExpressionRef,
        scope_id: str,
        sender_ref: str,
        source_message_id: str,
    ) -> None:
        now = message.timestamp.isoformat()
        fingerprint = self.metadata_fingerprint(expression)
        tags_json = json.dumps(list(expression.tags), ensure_ascii=False, separators=(",", ":"))
        bootstrap = self._bootstrap_profile(expression)
        bootstrap_json = json.dumps(bootstrap.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        key = (
            message.platform.value,
            message.account_scope,
            scope_id,
            expression.kind.value,
            expression.native_id,
        )

        with self.database._lock, self.database._conn:
            existing = self.database._conn.execute(
                """SELECT fingerprint, semantic_profile_json, semantic_status
                   FROM perceptual_expression_assets
                   WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?""",
                key,
            ).fetchone()

            if existing is None:
                self.database._conn.execute(
                    """INSERT INTO perceptual_expression_assets(
                           platform, account_scope, scope_id, kind, native_id,
                           name, description, tags_json, semantic_profile_json, semantic_status,
                           fingerprint, fingerprint_source, semantic_revision,
                           first_seen_at, last_seen_at, seen_count, unique_senders_count,
                           last_sender_ref, last_source_message_id, metadata_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_visual', ?, 'transport_metadata', 0,
                                 ?, ?, 0, 0, ?, ?, ?)""",
                    (
                        *key,
                        expression.name,
                        expression.description,
                        tags_json,
                        bootstrap_json,
                        fingerprint,
                        now,
                        now,
                        sender_ref,
                        source_message_id,
                        json.dumps(
                            {
                                "format_type": expression.format_type,
                                "fingerprint_limitation": "transport_metadata_only",
                            },
                            separators=(",", ":"),
                        ),
                    ),
                )
            else:
                changed = str(existing["fingerprint"]) != fingerprint
                profile_json = existing["semantic_profile_json"] or bootstrap_json
                status = "stale" if changed else str(existing["semantic_status"])
                self.database._conn.execute(
                    """UPDATE perceptual_expression_assets
                       SET name=?, description=?, tags_json=?, semantic_profile_json=?,
                           semantic_status=?, fingerprint=?, fingerprint_source='transport_metadata',
                           last_seen_at=?, last_sender_ref=?, last_source_message_id=?,
                           visual_claim_token=CASE WHEN ? THEN NULL ELSE visual_claim_token END,
                           visual_claimed_until=CASE WHEN ? THEN NULL ELSE visual_claimed_until END,
                           visual_defer_until=CASE WHEN ? THEN NULL ELSE visual_defer_until END,
                           visual_last_error_code=CASE WHEN ? THEN NULL ELSE visual_last_error_code END
                       WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?""",
                    (
                        expression.name,
                        expression.description,
                        tags_json,
                        profile_json,
                        status,
                        fingerprint,
                        now,
                        sender_ref,
                        source_message_id,
                        int(changed),
                        int(changed),
                        int(changed),
                        int(changed),
                        *key,
                    ),
                )

            self.database._conn.execute(
                """INSERT INTO perceptual_expression_observations(
                       platform, account_scope, scope_id, kind, native_id,
                       source_message_id, sender_ref, observed_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(platform, account_scope, scope_id, kind, native_id, source_message_id)
                   DO NOTHING""",
                (*key, source_message_id, sender_ref, now),
            )

            counts = self.database._conn.execute(
                """SELECT COUNT(*) AS seen_count, COUNT(DISTINCT sender_ref) AS sender_count,
                          MAX(observed_at) AS last_seen
                   FROM perceptual_expression_observations
                   WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?""",
                key,
            ).fetchone()
            sender_count = self.database._conn.execute(
                """SELECT COUNT(*) AS seen_count, MIN(observed_at) AS first_seen, MAX(observed_at) AS last_seen
                   FROM perceptual_expression_observations
                   WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?
                     AND sender_ref=?""",
                (*key, sender_ref),
            ).fetchone()
            self.database._conn.execute(
                """INSERT INTO perceptual_expression_senders(
                       platform, account_scope, scope_id, kind, native_id,
                       sender_ref, first_seen_at, last_seen_at, seen_count
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(platform, account_scope, scope_id, kind, native_id, sender_ref)
                   DO UPDATE SET first_seen_at=excluded.first_seen_at,
                                 last_seen_at=excluded.last_seen_at,
                                 seen_count=excluded.seen_count""",
                (
                    *key,
                    sender_ref,
                    str(sender_count["first_seen"] or now),
                    str(sender_count["last_seen"] or now),
                    int(sender_count["seen_count"] or 0),
                ),
            )
            self.database._conn.execute(
                """UPDATE perceptual_expression_assets
                   SET seen_count=?, unique_senders_count=?, last_seen_at=?
                   WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?""",
                (
                    int(counts["seen_count"] or 0),
                    int(counts["sender_count"] or 0),
                    str(counts["last_seen"] or now),
                    *key,
                ),
            )

    def _scope_records(
        self,
        *,
        platform: MessagingPlatform,
        account_scope: str,
        scope_id: str,
    ) -> dict[tuple[str, str], PerceptualExpressionRecord]:
        with self.database._lock:
            rows = self.database._conn.execute(
                """SELECT * FROM perceptual_expression_assets
                   WHERE platform=? AND account_scope=? AND scope_id=?
                   ORDER BY last_seen_at DESC LIMIT ?""",
                (platform.value, account_scope, scope_id, self.MAX_SCOPE_RECORDS),
            ).fetchall()
        records = [self._record_from_row(row) for row in rows]
        return {(record.kind, record.native_id): record for record in records}

    @staticmethod
    def _bootstrap_profile(expression: ExpressionRef) -> ExpressionSemanticProfile:
        parts: list[str] = []
        if expression.name:
            parts.append(f"nome: {expression.name}")
        if expression.description:
            parts.append(f"descrição: {expression.description}")
        if expression.tags:
            parts.append("tags: " + ", ".join(expression.tags[:8]))
        description = "; ".join(parts).strip()
        if len(description) > 250:
            description = description[:247].rstrip() + "..."
        return ExpressionSemanticProfile(
            short_description=description or "Expressão recebida; significado visual ainda não analisado.",
            visual_concepts=[],
            affect_tags=[],
            social_roles=[],
            possible_interpretations=list(expression.tags[:8]),
            register="casual",
            intensity=0.5,
            valence=0.0,
            arousal=0.5,
            confidence=0.20,
            source="transport_metadata",
        )

    @staticmethod
    def _row_key(row) -> tuple[str, str, str, str, str]:
        return (
            str(row["platform"]),
            str(row["account_scope"]),
            str(row["scope_id"]),
            str(row["kind"]),
            str(row["native_id"]),
        )

    @staticmethod
    def _claim_is_live(row, *, claim_token: str, now: datetime) -> bool:
        if not claim_token or str(row["visual_claim_token"] or "") != str(claim_token):
            return False
        raw_until = row["visual_claimed_until"]
        if not raw_until:
            return False
        try:
            claimed_until = datetime.fromisoformat(str(raw_until))
        except ValueError:
            return False
        if claimed_until.tzinfo is None or claimed_until.utcoffset() is None:
            return False
        return claimed_until > now

    @staticmethod
    def _normalize_sha256(value: str) -> str:
        candidate = str(value).strip().lower()
        if len(candidate) != 64 or any(char not in "0123456789abcdef" for char in candidate):
            raise ValueError("content_fingerprint must be a SHA-256 hex digest")
        return candidate

    @staticmethod
    def _normalize_mime(value: str) -> str:
        mime = str(value).strip().lower()
        if not mime or len(mime) > 100 or "/" not in mime:
            raise ValueError("content_mime must be a bounded media type")
        return mime

    def _normalize_content_size(self, value: int) -> int:
        size = int(value)
        if size < 1 or size > self.MAX_CONTENT_BYTES:
            raise ValueError("content_size_bytes outside visual enrichment bounds")
        return size

    @staticmethod
    def _bounded_label(value: str, limit: int, fallback: str) -> str:
        normalized = " ".join(str(value or "").split()).strip()
        return normalized[:limit] or fallback

    @staticmethod
    def _record_from_row(row) -> PerceptualExpressionRecord:
        profile_raw = row["semantic_profile_json"]
        profile = (
            ExpressionSemanticProfile.model_validate(json.loads(profile_raw))
            if profile_raw
            else None
        )
        return PerceptualExpressionRecord(
            platform=MessagingPlatform(str(row["platform"])),
            account_scope=str(row["account_scope"]),
            scope_id=str(row["scope_id"]),
            kind=str(row["kind"]),
            native_id=str(row["native_id"]),
            name=row["name"],
            description=row["description"],
            tags=list(json.loads(row["tags_json"] or "[]")),
            semantic_profile=profile,
            semantic_status=ExpressionSemanticStatus(str(row["semantic_status"])),
            fingerprint=str(row["fingerprint"]),
            fingerprint_source=str(row["fingerprint_source"]),
            content_fingerprint=row["content_fingerprint"],
            content_mime=row["content_mime"],
            content_size_bytes=(
                int(row["content_size_bytes"])
                if row["content_size_bytes"] is not None
                else None
            ),
            semantic_revision=int(row["semantic_revision"] or 0),
            first_seen_at=datetime.fromisoformat(str(row["first_seen_at"])),
            last_seen_at=datetime.fromisoformat(str(row["last_seen_at"])),
            seen_count=int(row["seen_count"] or 0),
            unique_senders_count=int(row["unique_senders_count"] or 0),
            last_sender_ref=row["last_sender_ref"],
            last_source_message_id=row["last_source_message_id"],
            last_visual_analysis_at=(
                datetime.fromisoformat(str(row["last_visual_analysis_at"]))
                if row["last_visual_analysis_at"]
                else None
            ),
            last_visual_analysis_fingerprint=row["last_visual_analysis_fingerprint"],
            last_visual_analysis_content_fingerprint=row[
                "last_visual_analysis_content_fingerprint"
            ],
            visual_provider=row["visual_provider"],
            visual_model=row["visual_model"],
            visual_claimed_until=(
                datetime.fromisoformat(str(row["visual_claimed_until"]))
                if row["visual_claimed_until"]
                else None
            ),
            visual_attempt_count=int(row["visual_attempt_count"] or 0),
            visual_last_error_code=row["visual_last_error_code"],
            visual_defer_until=(
                datetime.fromisoformat(str(row["visual_defer_until"]))
                if row["visual_defer_until"]
                else None
            ),
            metadata=dict(json.loads(row["metadata_json"] or "{}")),
        )

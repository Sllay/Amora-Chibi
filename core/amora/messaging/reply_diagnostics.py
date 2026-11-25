"""Bounded process-local telemetry for the durable text pilot.

Only fixed stages/outcomes and numeric aggregates. No raw exception messages,
provider text, prompts, tokens, native IDs or URL fields. Durable counts come
from the repository, not these reset-on-restart observations.
"""
from collections import Counter, deque
import logging
import math
from uuid import UUID

_LOG = logging.getLogger("amora.messaging.reply_diagnostics")
_OUTCOMES = frozenset({"pending", "running", "retry_wait", "ready", "blocked",
                       "cancelled", "sent", "unknown", "caller_cancelled"})
_REASONS = frozenset({"PROVIDER_TEMPORARY", "PROVIDER_BACKOFF", "GENERATION_TIMEOUT",
                      "STORAGE_TEMPORARY", "CONFIGURATION_BLOCKED", "CONTEXT_BLOCKED",
                      "AUTHORIZATION_REVOKED", "SOURCE_INVALIDATED", "DELIVERY_BLOCKED"})
_NEXT = {"retry_wait":"retry_when_due", "ready":"await_transport_ack",
         "sent":"none", "blocked":"operator_review", "cancelled":"none",
         "caller_cancelled":"recover_lease", "unknown":"inspect_storage",
         "running":"recover_lease", "pending":"await_claim"}


class ReplyDiagnostics:
    def __init__(self):
        self._attempts = Counter()
        self._reasons = Counter()
        self._duration = deque(maxlen=256)
        self._receipts = Counter()
        self._recovered_ready = 0

    def receipt(self, duplicate):
        self._receipts["duplicate_ack" if duplicate else "new_ack"] += 1

    def attempt(self, turn_id, attempt, outcome, reason, duration_ms):
        outcome = outcome if outcome in _OUTCOMES else "unknown"
        reason = reason if isinstance(reason, str) and reason in _REASONS else "NONE_OR_UNKNOWN"
        self._attempts[outcome] += 1
        self._reasons[reason] += 1
        if attempt > 1 and outcome == "ready":
            self._recovered_ready += 1
        if math.isfinite(duration_ms) and duration_ms >= 0:
            self._duration.append(duration_ms)
        try:
            safe_id = str(UUID(str(turn_id)))
        except (ValueError, TypeError, AttributeError):
            safe_id = "invalid"
        _LOG.info("reply_attempt turn_id=%s job_id=%s stage=dispatcher attempt=%d "
                  "outcome=%s reason=%s next=%s duration_ms=%.1f",
                  safe_id, safe_id, attempt, outcome, reason, _NEXT[outcome], duration_ms)

    def snapshot(self):
        samples = sorted(self._duration)
        def percentile(q):
            return round(samples[max(0, math.ceil(len(samples)*q)-1)], 2) if samples else None
        return {
            "scope":"process_since_start_durable_text_only",
            "receipt_counts":dict(self._receipts),
            "attempt_outcomes":dict(self._attempts),
            "attempt_reasons":dict(self._reasons),
            "recovered_ready_attempts":self._recovered_ready,
            "attempt_duration_ms":{"sample_count":len(samples), "window_max":256,
                                   "p50":percentile(.5), "p95":percentile(.95)},
            "delivery_note":"ready is not sent; durable counts are authoritative",
        }

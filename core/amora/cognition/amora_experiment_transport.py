"""Operator-only NVIDIA experiment transport policy and safe failure diagnostics.

Both A/B candidates use identical bounded deadlines. Production transport is
intentionally unchanged. Failure details contain no credentials or model text.
"""
from __future__ import annotations

from typing import Any

from .providers_nvidia import (
    NvidiaMistralLarge4ProductionProvider,
    NvidiaStreamTimeout,
)

EXPERIMENT_IDLE_SECONDS = 100.0
EXPERIMENT_MAX_TOTAL_SECONDS = 225.0
_SAFE_STAGES = {
    "waiting_headers", "waiting_first_event", "reasoning_only",
    "visible_partial", "completed",
}
_SAFE_TIMEOUT_KINDS = {"idle", "total_safety"}
_DIAGNOSTIC_KEYS = (
    "elapsed_ms", "silence_ms", "first_event_ms", "first_visible_ms",
    "first_reasoning_ms", "reasoning_events", "visible_events",
)


def experiment_provider(secrets: Any, *, model: str) -> NvidiaMistralLarge4ProductionProvider:
    """Only experimental runners receive extended SSE deadlines."""
    return NvidiaMistralLarge4ProductionProvider(
        secrets,
        model=model,
        timeout_seconds=EXPERIMENT_IDLE_SECONDS,
        max_total_seconds=EXPERIMENT_MAX_TOTAL_SECONDS,
    )


def classify_provider_failure(item: dict[str, Any], exc: Exception) -> None:
    """Never label failures prior to JSON/RealizationValidator as Core rejection."""
    item["validated"] = False
    item["validation_status"] = "not_run"
    item["diagnostic_only"] = True
    if isinstance(exc, NvidiaStreamTimeout):
        details = exc.diagnostics
        stage = details.get("stage")
        kind = details.get("timeout_kind")
        item["error_code"] = "EXPERIMENT_NVIDIA_TIMEOUT"
        item["failure_stage"] = stage if stage in _SAFE_STAGES else "unknown"
        item["timeout_kind"] = kind if kind in _SAFE_TIMEOUT_KINDS else "unknown"
        safe = {}
        for key in _DIAGNOSTIC_KEYS:
            value = details.get(key)
            if value is None or type(value) in (int, float):
                safe[key] = value
        item["stream_diagnostics"] = safe
        item["http_status"] = None
        return
    status = getattr(exc, "status_code", None)
    item["http_status"] = status if type(status) is int else None
    item["rate_limit_observed"] = status == 429
    item["error_code"] = (
        "EXPERIMENT_RATE_LIMITED" if status == 429
        else "EXPERIMENT_PROVIDER_FAILURE"
    )

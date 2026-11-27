"""Observe request envelopes without logging private content or credentials."""
import logging

import httpx

from .context_budget import estimate_payload


def log_payload_metrics(provider, url, payload, *, phase):
    # Use the HTTP client's JSON encoding, not character counts, to measure
    # the actual body. This builds a local request only; it sends nothing.
    body_bytes = len(httpx.Request("POST", url, json=payload).content)
    from amora.latency_trace import increment,max_ms
    increment("provider_payload_count")
    increment("provider_request_bytes_total",body_bytes)
    max_ms("provider_request_bytes_max",body_bytes)
    increment("provider_estimated_input_tokens_total",estimate_payload(payload))
    messages = payload.get("messages", payload.get("input", []))
    logging.getLogger("amora.cognition.payload").info(
        "provider_payload", extra={
            "provider": provider,
            "model": payload.get("model"),
            "phase": phase,
            "body_bytes": body_bytes,
            "estimated_input_tokens": estimate_payload(payload),
            "message_count": len(messages) if isinstance(messages, list) else 0,
            "max_output_tokens": payload.get("max_output_tokens", payload.get("max_tokens")),
            "source_only": phase == "memory_extraction",
        })

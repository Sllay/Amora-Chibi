"""Content-free measurements for a single Numes SSE attempt.

Times are observed by the local client, not upstream inference timings.
No raw reasoning, generated text, headers or usage objects are retained.
"""
from __future__ import annotations

import asyncio
from time import perf_counter


def _token_count(value):
    return value if type(value) is int and value >= 0 else None


def _reasoning_chars(delta):
    # Providers may alias the same reasoning in multiple fields. Count one
    # representation only; encrypted/opaque detail payloads are not text.
    for key in ("reasoning_content", "reasoning"):
        value = delta.get(key)
        if isinstance(value, str) and value:
            return len(value)
    details = delta.get("reasoning_details")
    if not isinstance(details, list):
        return 0
    return sum(len(value) for detail in details if isinstance(detail, dict)
               for value in [detail.get("text") or detail.get("summary")]
               if isinstance(value, str))


class NumesStreamDiagnostics:
    """Numeric-only per-attempt observations and bounded loop-lag sampling."""

    def __init__(self, *, clock=perf_counter):
        self.clock = clock
        self.started = clock()
        self.metrics = {
            "sse_data_events": 0, "sse_max_gap_ms": 0.0,
            "reasoning_deltas": 0, "reasoning_chars": 0,
            "content_deltas": 0, "content_chars": 0,
            "content_max_gap_ms": 0.0, "loop_samples": 0,
            "loop_lag_max_ms": 0.0, "loop_lag_sum_ms": 0.0,
            "loop_lag_over_100ms": 0,
            "loop_lag_before_content_max_ms": 0.0,
            "loop_lag_after_content_max_ms": 0.0,
        }
        self._last_event = None
        self._last_content = None
        self._loop = None
        self._handle = None
        self._due = None
        self._interval = 0.1
        self._closed = False

    def elapsed(self):
        return round((self.clock() - self.started) * 1000, 2)

    def data_event(self, *, done=False):
        now = self.elapsed()
        if self._last_event is not None:
            self.metrics["sse_max_gap_ms"] = max(
                self.metrics["sse_max_gap_ms"], now - self._last_event)
        self._last_event = now
        self.metrics["sse_data_events"] += 1
        if done:
            self.metrics["done_ms"] = now

    def reasoning(self, delta):
        self.metrics["reasoning_deltas"] += 1
        self.metrics["reasoning_chars"] += _reasoning_chars(delta)
        now = self.elapsed()
        self.metrics.setdefault("first_reasoning_ms", now)
        self.metrics["last_reasoning_ms"] = now

    def content(self, value):
        now = self.elapsed()
        self.metrics["content_deltas"] += 1
        self.metrics["content_chars"] += len(value)
        self.metrics.setdefault("first_content_ms", now)
        if self._last_content is not None:
            self.metrics["content_max_gap_ms"] = max(
                self.metrics["content_max_gap_ms"], now - self._last_content)
        self._last_content = now
        self.metrics["last_content_ms"] = now

    def finish(self):
        self.metrics["finish_reason_ms"] = self.elapsed()

    def start_sampler(self):
        self._loop = asyncio.get_running_loop()
        self._schedule()

    def _schedule(self):
        self._due = self._loop.time() + self._interval
        self._handle = self._loop.call_at(self._due, self._sample)

    def _record_lag(self):
        lag = max(0.0, (self._loop.time() - self._due) * 1000)
        m = self.metrics
        m["loop_samples"] += 1
        m["loop_lag_max_ms"] = max(m["loop_lag_max_ms"], lag)
        m["loop_lag_sum_ms"] += lag
        if lag >= 100:
            m["loop_lag_over_100ms"] += 1
        phase = "after" if self._last_content is not None else "before"
        key = "loop_lag_" + phase + "_content_max_ms"
        m[key] = max(m[key], lag)

    def _sample(self):
        if not self._closed:
            self._record_lag()
            self._schedule()

    def close(self, *, usage=None, parsed=None):
        if not self._closed:
            self._closed = True
            if self._handle is not None:
                self._handle.cancel()
                # Capture an overdue callback even if the stream finished before
                # the event loop could dispatch it.
                if self._loop.time() >= self._due:
                    self._record_lag()
        m = self.metrics
        if "last_reasoning_ms" in m:
            m["reasoning_span_ms"] = m["last_reasoning_ms"] - m["first_reasoning_ms"]
        if self._last_content is not None:
            m["content_span_ms"] = self._last_content - m["first_content_ms"]
            if "last_reasoning_ms" in m:
                m["last_reasoning_to_content_ms"] = m["first_content_ms"] - m["last_reasoning_ms"]
            if "done_ms" in m:
                m["last_content_to_done_ms"] = m["done_ms"] - self._last_content
        if "finish_reason_ms" in m and "done_ms" in m:
            m["finish_to_done_ms"] = m["done_ms"] - m["finish_reason_ms"]
        usage = usage if isinstance(usage, dict) else {}
        details = usage.get("completion_tokens_details")
        details = details if isinstance(details, dict) else {}
        reasoning = _token_count(details.get("reasoning_tokens"))
        if reasoning is None:
            reasoning = _token_count(usage.get("reasoning_tokens"))
        for name, value in (
            ("prompt_tokens_reported", _token_count(usage.get("prompt_tokens"))),
            ("completion_tokens_reported", _token_count(usage.get("completion_tokens"))),
            ("reasoning_tokens_reported", reasoning),
        ):
            if value is not None:
                m[name] = value
        # Count only the raw model's structured surface text, not final Discord
        # rendering. Do not retain or emit any of the strings.
        if isinstance(parsed, dict) and isinstance(parsed.get("moves"), list):
            m["surface_chars"] = sum(len(move["text"]) for move in parsed["moves"]
                                     if isinstance(move, dict) and isinstance(move.get("text"), str))
        return {key: round(value, 3) if isinstance(value, float) else value
                for key, value in m.items()}

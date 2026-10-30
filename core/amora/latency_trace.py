from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from typing import Any


_trace: ContextVar[dict[str, Any] | None] = ContextVar("amora_latency_trace", default=None)


@contextmanager
def bind_latency_trace() -> Iterator[dict[str, Any]]:
    """Bind one mutable, request-scoped latency trace.

    The trace is diagnostic-only: it is not persisted, does not carry user text,
    and contains only bounded timings/counters/provider labels safe for Web labs.
    A mutable object is intentional so downstream task/context copies still write
    into the same request trace collected by the outer Web middleware.
    """
    trace: dict[str, Any] = {}
    token = _trace.set(trace)
    try:
        yield trace
    finally:
        _trace.reset(token)


def current_trace() -> dict[str, Any] | None:
    return _trace.get()


def set_value(name: str, value: Any) -> None:
    trace = _trace.get()
    if trace is not None:
        trace[name] = value


def set_ms(name: str, value: float) -> None:
    set_value(name, round(max(0.0, float(value)), 3))


def add_ms(name: str, value: float) -> None:
    trace = _trace.get()
    if trace is None:
        return
    trace[name] = round(max(0.0, float(trace.get(name, 0.0))) + max(0.0, float(value)), 3)


def increment(name: str, amount: int = 1) -> None:
    trace = _trace.get()
    if trace is not None:
        trace[name] = int(trace.get(name, 0)) + int(amount)


def max_ms(name: str, value: float) -> None:
    trace = _trace.get()
    if trace is None:
        return
    candidate = max(0.0, float(value))
    trace[name] = round(max(float(trace.get(name, 0.0)), candidate), 3)


def elapsed_ms(started: float) -> float:
    return (perf_counter() - started) * 1000.0


def install_remote_storage_probe() -> None:
    """Instrument libSQL/Hrana pipeline latency only while a trace is bound.

    This preserves the storage adapter contract and never inspects SQL text or
    parameters. It records aggregate pipeline wall time/call count/max only.
    """
    from amora.storage.remote import RemoteConnection

    original = RemoteConnection._pipeline
    if getattr(original, "_amora_latency_probe", False):
        return

    def traced_pipeline(self, requests):
        started = perf_counter()
        try:
            return original(self, requests)
        finally:
            elapsed = elapsed_ms(started)
            add_ms("remote_sql_ms", elapsed)
            increment("remote_sql_calls")
            max_ms("remote_sql_max_ms", elapsed)

    traced_pipeline._amora_latency_probe = True  # type: ignore[attr-defined]
    RemoteConnection._pipeline = traced_pipeline

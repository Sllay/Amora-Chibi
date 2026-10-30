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


def _pipeline_class(requests: list[dict[str, Any]]) -> str:
    """Classify one Hrana pipeline without exposing SQL text or arguments.

    Only the first SQL verb of non-control statements is inspected. No SQL text,
    identifiers, parameters, user content or values are retained in the trace.
    A pipeline is counted exactly once so read/write/txn/other call counts sum to
    the aggregate remote_sql_calls metric.
    """
    verbs: list[str] = []
    for request in requests:
        if request.get("type") != "execute":
            continue
        sql = str(request.get("stmt", {}).get("sql") or "").lstrip()
        if not sql:
            continue
        verbs.append(sql.split(None, 1)[0].upper())

    controls = {"BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE"}
    data_verbs = [verb for verb in verbs if verb not in controls]
    if not data_verbs:
        return "txn"
    if any(verb in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP", "VACUUM"} for verb in data_verbs):
        return "write"
    if all(verb in {"SELECT", "WITH", "EXPLAIN", "PRAGMA"} for verb in data_verbs):
        return "read"
    return "other"


def install_remote_storage_probe() -> None:
    """Instrument libSQL/Hrana pipeline latency only while a trace is bound.

    This preserves the storage adapter contract. The v3 classifier observes only
    SQL verbs so Safari can distinguish read, write and transaction-control RTTs;
    it never records SQL text, identifiers, arguments, user text or provider data.
    """
    from amora.storage.remote import RemoteConnection

    original = RemoteConnection._pipeline
    if getattr(original, "_amora_latency_probe", False):
        return

    def traced_pipeline(self, requests):
        category = _pipeline_class(requests)
        started = perf_counter()
        try:
            return original(self, requests)
        finally:
            elapsed = elapsed_ms(started)
            add_ms("remote_sql_ms", elapsed)
            increment("remote_sql_calls")
            max_ms("remote_sql_max_ms", elapsed)
            add_ms(f"remote_sql_{category}_ms", elapsed)
            increment(f"remote_sql_{category}_calls")

    traced_pipeline._amora_latency_probe = True  # type: ignore[attr-defined]
    RemoteConnection._pipeline = traced_pipeline

from __future__ import annotations

import asyncio
import inspect
import hashlib
import json
import logging
from functools import wraps
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from typing import Any
from uuid import UUID


_trace: ContextVar[dict[str, Any] | None] = ContextVar("amora_latency_trace", default=None)
_stage: ContextVar[str | None] = ContextVar("amora_latency_stage", default=None)
_LOG = logging.getLogger("amora.performance")


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
    stage_token = _stage.set(None)
    try:
        yield trace
    finally:
        _stage.reset(stage_token)
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
        if current_trace() is None:
            return original(self, requests)
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
            stage = _stage.get()
            if stage is not None:
                add_ms(f"stage_{stage}_sql_ms", elapsed)
                increment(f"stage_{stage}_sql_calls")
            increment("remote_sql_statements", sum(item.get("type") == "execute" for item in requests))
            # Exact SQL shape repetition is diagnostic, not proof of redundancy.
            # Keep only transient hashes; never log them or inspect bound values.
            shapes = current_trace().setdefault("_sql_shapes", set())
            for item in requests:
                if item.get("type") != "execute":
                    continue
                sql = item.get("stmt", {}).get("sql") or ""
                verb = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
                if verb not in {"SELECT", "WITH", "INSERT", "UPDATE", "DELETE", "REPLACE"}:
                    continue
                shape = hashlib.sha256(sql.encode("utf-8")).digest()
                if shape in shapes:
                    increment("remote_sql_repeated_shapes")
                elif len(shapes) < 512:
                    shapes.add(shape)
                    increment("remote_sql_unique_shapes")

    traced_pipeline._amora_latency_probe = True  # type: ignore[attr-defined]
    RemoteConnection._pipeline = traced_pipeline


@contextmanager
def span(name: str):
    """Inclusive stage time; remote RTTs are assigned only to the innermost stage."""
    if current_trace() is None:
        yield
        return
    token = _stage.set(name)
    started = perf_counter()
    try:
        yield
    finally:
        add_ms(f"stage_{name}_ms", elapsed_ms(started))
        increment(f"stage_{name}_calls")
        _stage.reset(token)


def trace_stage(name: str):
    """Trace a fixed, code-defined stage without changing its return/exception contract."""
    def decorate(fn):
        if inspect.iscoroutinefunction(fn):
            @wraps(fn)
            async def async_wrapped(*args, **kwargs):
                with span(name):
                    return await fn(*args, **kwargs)
            return async_wrapped
        @wraps(fn)
        def wrapped(*args, **kwargs):
            with span(name):
                return fn(*args, **kwargs)
        return wrapped
    return decorate


@contextmanager
def trace_lock(lock):
    """Record acquisition wait separately from time holding a synchronous lock."""
    if current_trace() is None:
        with lock:
            yield
        return
    started = perf_counter()
    with lock:
        add_ms("lock_wait_ms", elapsed_ms(started))
        increment("lock_acquisitions")
        held = perf_counter()
        try:
            yield
        finally:
            add_ms("lock_held_ms", elapsed_ms(held))


def _safe_uuid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return "-"


def log_trace(kind: str, trace: dict[str, Any], *, turn_id=None, outbound_id=None, outcome: str = "ok"):
    # No text, SQL, arguments, headers, model output or arbitrary trace values.
    numeric = {key: value for key, value in trace.items()
               if type(value) in (int, float)}
    _LOG.info("performance_trace kind=%s turn_id=%s outbound_id=%s outcome=%s metrics=%s",
              kind, _safe_uuid(turn_id), _safe_uuid(outbound_id), outcome,
              json.dumps(numeric, sort_keys=True, separators=(",", ":")))


def trace_messaging_turn(fn):
    """One trace per owned logical turn, not per HTTP retry/waiter."""
    @wraps(fn)
    async def wrapped(self, message):
        if message.privacy_scope.value in {"AUTH", "SECRET"}:
            return await fn(self, message)
        install_remote_storage_probe()
        with bind_latency_trace() as trace:
            started = perf_counter()
            interval = 0.1
            loop = asyncio.get_running_loop()
            last = [loop.time()]
            def record_lag(now):
                lag = (now - last[0] - interval) * 1000
                max_ms("event_loop_lag_max_ms", lag)
                phase = trace.get("_critical_phase")
                if phase in {"ingress", "cognition", "delivery"}:
                    max_ms("event_loop_lag_observed_" + phase + "_ms", lag)

            async def sample_lag():
                while True:
                    await asyncio.sleep(max(0.0, last[0] + interval - loop.time()))
                    now = loop.time()
                    record_lag(now)
                    increment("event_loop_samples")
                    last[0] = now
            sampler = asyncio.create_task(sample_lag(), name="amora-performance-lag")
            outcome = "ok"
            result = None
            try:
                result = await fn(self, message)
                return result
            except BaseException:
                outcome = "error"
                raise
            finally:
                set_ms("turn_total_ms", elapsed_ms(started))
                record_lag(loop.time())
                sampler.cancel()
                # Cancelled sampler owns no resources and never writes after cancellation.
                outbound = result.outbound[0] if result is not None and result.outbound else None
                log_trace("messaging", trace, turn_id=self._turn_id(message),
                          outbound_id=outbound.id if outbound is not None else None, outcome=outcome)
                # Short independent record for log viewers that truncate the
                # full trace. Lag phase is where the sampler resumed, not
                # necessarily where the stall began.
                keys = (
                    "turn_total_ms", "messaging_pre_cognition_ms", "messaging_cognition_ms",
                    "context_compile_ms", "stage_interaction_appraisal_ms",
                    "stage_generation_lease_wait_ms", "stage_cognitive_db_lane_ms",
                    "stage_cognitive_db_lane_calls", "stage_cognitive_db_lane_wait_ms",
                    "stage_validated_plan_ms", "stage_provider_attempt_ms",
                    "remote_sql_calls", "remote_sql_ms", "event_loop_lag_max_ms",
                    "event_loop_lag_observed_ingress_ms", "event_loop_lag_observed_cognition_ms",
                    "event_loop_lag_observed_delivery_ms",
                )
                log_trace("critical_path", {key: trace[key] for key in keys if key in trace},
                          turn_id=self._turn_id(message),
                          outbound_id=outbound.id if outbound is not None else None, outcome=outcome)
    return wrapped

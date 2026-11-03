from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(slots=True)
class ProviderCircuit:
    provider: str
    task_class: str
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    open_until: datetime | None = None
    probe_lease_until: datetime | None = None
    last_failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class CircuitRoutingDecision:
    excluded_names: frozenset[str]
    half_open_probe_names: frozenset[str]


class ProviderCircuitRegistry:
    """In-memory provider circuit state, keyed by provider + task class.

    The legacy router still owns ranking and quota. This registry only decides
    whether a provider is CLOSED, OPEN, or eligible for one HALF_OPEN probe.
    A short probe lease prevents concurrent turns from all probing the same
    provider immediately after its cooldown expires.
    """

    def __init__(self, *, half_open_probe_lease_seconds: float = 5.0) -> None:
        self.half_open_probe_lease_seconds = max(0.1, float(half_open_probe_lease_seconds))
        self._records: dict[tuple[str, str], ProviderCircuit] = {}

    def _key(self, provider: str, task_class: str) -> tuple[str, str]:
        return (str(provider), str(task_class))

    def record(self, provider: str, task_class: str) -> ProviderCircuit:
        key = self._key(provider, task_class)
        current = self._records.get(key)
        if current is None:
            current = ProviderCircuit(provider=key[0], task_class=key[1])
            self._records[key] = current
        return current

    def open(
        self,
        provider: str,
        task_class: str,
        *,
        until: datetime,
        failure_count: int,
        failure_code: str | None = None,
    ) -> ProviderCircuit:
        current = self.record(provider, task_class)
        current.state = CircuitState.OPEN
        current.failure_count = max(1, int(failure_count))
        current.open_until = until
        current.probe_lease_until = None
        current.last_failure_code = failure_code
        return current

    def close(self, provider: str, task_class: str) -> ProviderCircuit:
        current = self.record(provider, task_class)
        current.state = CircuitState.CLOSED
        current.failure_count = 0
        current.open_until = None
        current.probe_lease_until = None
        current.last_failure_code = None
        return current

    def routing_decision(self, task_class: str, *, now: datetime) -> CircuitRoutingDecision:
        excluded: set[str] = set()
        probes: set[str] = set()
        lease = timedelta(seconds=self.half_open_probe_lease_seconds)

        for (provider, record_task), current in self._records.items():
            if record_task != str(task_class):
                continue
            if current.state is CircuitState.CLOSED:
                continue
            if current.state is CircuitState.OPEN:
                if current.open_until is not None and current.open_until > now:
                    excluded.add(provider)
                    continue
                current.state = CircuitState.HALF_OPEN
                current.probe_lease_until = now + lease
                probes.add(provider)
                continue
            if current.state is CircuitState.HALF_OPEN:
                if current.probe_lease_until is not None and current.probe_lease_until > now:
                    excluded.add(provider)
                    continue
                current.probe_lease_until = now + lease
                probes.add(provider)

        return CircuitRoutingDecision(
            excluded_names=frozenset(excluded),
            half_open_probe_names=frozenset(probes),
        )

    def snapshot(self, *, now: datetime) -> dict[str, dict[str, object]]:
        result: dict[str, dict[str, object]] = {}
        for (provider, task_class), current in sorted(self._records.items()):
            key = f"{provider}:{task_class}"
            result[key] = {
                "provider": provider,
                "task_class": task_class,
                "state": current.state.value,
                "failure_count": current.failure_count,
                "open_until": current.open_until.isoformat() if current.open_until else None,
                "probe_lease_until": current.probe_lease_until.isoformat() if current.probe_lease_until else None,
                "last_failure_code": current.last_failure_code,
                "open_remaining_seconds": (
                    max(0.0, (current.open_until - now).total_seconds())
                    if current.open_until is not None and current.state is CircuitState.OPEN
                    else 0.0
                ),
            }
        return result

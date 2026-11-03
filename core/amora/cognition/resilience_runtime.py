from __future__ import annotations

from amora.models import TaskClass

from .identity_runtime import CognitiveEngine as IdentityAwareCognitiveEngine
from .provider_circuit import ProviderCircuitRegistry
from .provider_resilience import retry_after_seconds, wrap_resilient_provider


class CognitiveEngine(IdentityAwareCognitiveEngine):
    """Identity-aware cognition with bounded recovery and explicit circuits."""

    _CIRCUIT_TASK = TaskClass.SOCIAL_DIALOGUE.value

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._provider_circuits = ProviderCircuitRegistry()

    def state(self) -> dict[str, object]:
        state = super().state()
        state["cognitive_provider_circuits"] = self._provider_circuits.snapshot(now=self.clock.now())
        return state

    def register_provider(self, provider) -> None:
        # Let the identity runtime register/replace all canonical provider
        # adapters first (including automatic Gemini failover), then decorate the
        # final structured adapters in place. Router descriptors stay unchanged.
        super().register_provider(provider)
        for name, current in list(self._providers.items()):
            wrapped = wrap_resilient_provider(current)
            if wrapped is not current:
                self._providers[name] = wrapped

    def _backoff(
        self,
        descriptor,
        *,
        status_code: int | None,
        retry_after: str | None = None,
    ) -> None:
        # Normalize both legal HTTP Retry-After forms before delegating to the
        # legacy cooldown calculator. Long server-requested cooldowns are kept at
        # the outer Core layer, never slept through inside an interactive turn.
        parsed = retry_after_seconds(retry_after, now=self.clock.now())
        normalized = str(parsed) if parsed is not None else None
        super()._backoff(descriptor, status_code=status_code, retry_after=normalized)

        until = self._provider_cooldowns.get(descriptor.name)
        if until is None:
            return
        failure_count = self._provider_failures.get(descriptor.name, 1)
        failure_code = f"HTTP_{status_code}" if status_code is not None else "PROVIDER_FAILURE"
        self._provider_circuits.open(
            descriptor.name,
            self._CIRCUIT_TASK,
            until=until,
            failure_count=failure_count,
            failure_code=failure_code,
        )

    def _provider_succeeded(
        self,
        descriptor,
        remaining_requests: int | None,
        remaining_tokens: int | None,
    ) -> None:
        super()._provider_succeeded(descriptor, remaining_requests, remaining_tokens)
        # A successful HALF_OPEN probe closes the circuit. If the success response
        # simultaneously reports exhausted quota, preserve the quota cooldown as
        # OPEN rather than immediately routing more work to that provider.
        until = self._provider_cooldowns.get(descriptor.name)
        if until is not None:
            self._provider_circuits.open(
                descriptor.name,
                self._CIRCUIT_TASK,
                until=until,
                failure_count=1,
                failure_code="QUOTA_EXHAUSTED",
            )
            return
        self._provider_circuits.close(descriptor.name, self._CIRCUIT_TASK)

    def _cooldown_names(self) -> set[str]:
        now = self.clock.now()
        decision = self._provider_circuits.routing_decision(self._CIRCUIT_TASK, now=now)

        # OPEN providers remain excluded. An expired OPEN gets exactly one
        # HALF_OPEN routing lease; clear the legacy cooldown/quota only for that
        # probe so the normal router can select it. Concurrent turns see the
        # active probe lease and keep the provider excluded.
        for name in decision.half_open_probe_names:
            self._provider_cooldowns.pop(name, None)
            if self.router.descriptor(name) is not None:
                self.router.set_quota(name, None)

        # Preserve any legacy cooldown that predates circuit initialization. This
        # is mostly defensive for hot-reload/migration scenarios.
        legacy_excluded = {
            name
            for name, until in self._provider_cooldowns.items()
            if until > now
        }
        return set(decision.excluded_names) | legacy_excluded

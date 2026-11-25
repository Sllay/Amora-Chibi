from __future__ import annotations

from datetime import timedelta
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

    def _structured_failure_details(self, provider_name: str) -> tuple[str | None, str | None]:
        provider = self._providers.get(provider_name)
        semantic_code = getattr(provider, "last_structured_error_code", None)
        if not semantic_code:
            return None, None
        return str(semantic_code), "PROVIDER_STRUCTURED_OUTPUT_INVALID"

    def _record_provider_failure(
        self,
        request,
        descriptor,
        *,
        error_code: str,
        status_code: int | None,
        started: float,
    ) -> None:
        semantic_code, provider_failure_code = self._structured_failure_details(descriptor.name)
        normalized_error = provider_failure_code or error_code

        if provider_failure_code is not None:
            rejection = getattr(self, "_last_realization_rejection", None)
            if isinstance(rejection, dict) and rejection.get("provider") == descriptor.name:
                updated = dict(rejection)
                updated["code"] = semantic_code
                updated["provider_failure_code"] = provider_failure_code
                self._last_realization_rejection = updated

        super()._record_provider_failure(
            request,
            descriptor,
            error_code=normalized_error,
            status_code=status_code,
            started=started,
        )

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
        # The legacy 300s ceiling must not permit another dialogue to hit a
        # provider before a valid server-specified cooldown expires.
        if parsed is not None and parsed > 300:
            until = self.clock.now() + timedelta(seconds=min(604800, parsed))
            self._provider_cooldowns[descriptor.name] = until
        if until is None:
            return
        failure_count = self._provider_failures.get(descriptor.name, 1)
        _, structured_failure_code = self._structured_failure_details(descriptor.name)
        failure_code = structured_failure_code or (
            f"HTTP_{status_code}" if status_code is not None else "PROVIDER_FAILURE"
        )
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
        excluded = self._provider_circuits.excluded_names(self._CIRCUIT_TASK, now=now)
        # Expiry restores router eligibility, but does not claim any probe.
        for name, until in list(self._provider_cooldowns.items()):
            if until <= now and name not in excluded:
                self._provider_cooldowns.pop(name, None)
                if self.router.descriptor(name) is not None:
                    self.router.set_quota(name, None)
            elif until > now:
                excluded.add(name)
        return excluded

    def _begin_structured_attempt(self, descriptor) -> bool:
        if descriptor.name in self._cooldown_names():
            return False
        return self._provider_circuits.begin_attempt(
            descriptor.name, self._CIRCUIT_TASK, now=self.clock.now())

    def _end_structured_attempt(self, descriptor) -> None:
        self._provider_circuits.end_attempt(
            descriptor.name, self._CIRCUIT_TASK, now=self.clock.now())

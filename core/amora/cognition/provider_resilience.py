from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .providers import ProviderError
from .realization import RealizationError, RealizationValidator, response_semantic_filter_enabled


_NON_RETRYABLE_STATUS = {400, 401, 403, 404, 405, 409, 422}


class StructuredRealizationProviderError(ProviderError):
    """Provider exhausted bounded repair and still violated realization contract."""

    def __init__(self, realization_error_code: str) -> None:
        super().__init__("provider realization remained invalid after bounded repair")
        self.realization_error_code = realization_error_code


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    code: str
    retryable: bool
    status_code: int | None = None
    retry_after_seconds: float | None = None


def retry_after_seconds(value: str | None, *, now: datetime | None = None) -> float | None:
    """Parse HTTP Retry-After as delay-seconds or an HTTP-date.

    Returns a non-negative delay. Invalid values intentionally become ``None`` so
    callers can fall back to their bounded exponential policy.
    """

    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        pass
    try:
        target = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0.0, (target - current).total_seconds())


def classify_provider_failure(
    exc: BaseException,
    *,
    now: datetime | None = None,
) -> ProviderFailure:
    """Classify transport/provider failures without leaking provider payloads."""

    if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)):
        return ProviderFailure("PROVIDER_TIMEOUT", True)
    if isinstance(exc, httpx.NetworkError):
        return ProviderFailure("PROVIDER_NETWORK", True)
    if isinstance(exc, StructuredRealizationProviderError):
        return ProviderFailure("PROVIDER_STRUCTURED_OUTPUT_INVALID", False)
    if isinstance(exc, ProviderError):
        status = exc.status_code
        retry_after = retry_after_seconds(exc.retry_after, now=now)
        if status in _NON_RETRYABLE_STATUS:
            return ProviderFailure(f"PROVIDER_HTTP_{status}", False, status, retry_after)
        if status == 429:
            return ProviderFailure("PROVIDER_RATE_LIMIT", True, status, retry_after)
        if status is not None and 500 <= status <= 599:
            return ProviderFailure("PROVIDER_UPSTREAM_5XX", True, status, retry_after)
        if status is None:
            return ProviderFailure("PROVIDER_TRANSIENT", True, None, retry_after)
        return ProviderFailure(f"PROVIDER_HTTP_{status}", False, status, retry_after)
    return ProviderFailure(type(exc).__name__, False)


class ResilientProviderProxy:
    """Bounded recovery proxy for provider realization.

    Transport recovery is always available and reuses the exact same immutable
    context. Structured-output repair is deliberately opt-in: direct callers keep
    the historical transport-only contract, while the social runtime enables one
    bounded repair when it decorates production structured providers.
    """

    def __init__(
        self,
        provider: Any,
        *,
        max_retries: int = 1,
        max_validation_retries: int = 0,
        enforce_dialogue_plan: bool = True,
        base_delay_seconds: float = 0.08,
        max_inline_retry_delay_seconds: float = 1.5,
        jitter_fraction: float = 0.20,
        random_fn: Callable[[], float] | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._provider = provider
        self.descriptor = provider.descriptor
        self.max_retries = max(0, int(max_retries))
        self.max_validation_retries = max(0, int(max_validation_retries))
        self.enforce_dialogue_plan = enforce_dialogue_plan
        self.base_delay_seconds = max(0.0, float(base_delay_seconds))
        self.max_inline_retry_delay_seconds = max(0.0, float(max_inline_retry_delay_seconds))
        self.jitter_fraction = min(1.0, max(0.0, float(jitter_fraction)))
        self._random = random_fn or random.random
        self._sleep = sleep_fn or asyncio.sleep
        self._last_structured_error_code: str | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    @property
    def last_structured_error_code(self) -> str | None:
        return self._last_structured_error_code

    def _bounded_backoff(self, attempt: int) -> float:
        raw = self.base_delay_seconds * (2 ** max(0, attempt - 1))
        if raw <= 0.0:
            return 0.0
        jitter = 1.0 - self.jitter_fraction + (2.0 * self.jitter_fraction * self._random())
        return min(self.max_inline_retry_delay_seconds, max(0.0, raw * jitter))

    def _retry_delay(self, failure: ProviderFailure, *, attempt: int) -> float | None:
        backoff = self._bounded_backoff(attempt)
        retry_after = failure.retry_after_seconds
        if retry_after is not None:
            if retry_after > self.max_inline_retry_delay_seconds:
                # Interactive dialogue must not sleep through a long provider
                # cooldown. Let the outer runtime record/open/fail over instead.
                return None
            return max(backoff, retry_after)
        return backoff

    async def realize(self, context):
        realize = getattr(self._provider, "realize", None)
        if not callable(realize):
            raise AttributeError("wrapped provider has no realize interface")

        self._last_structured_error_code = None
        transport_attempt = 0
        validation_attempt = 0
        while True:
            try:
                result = await realize(context)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure = classify_provider_failure(exc)
                if not failure.retryable or transport_attempt >= self.max_retries:
                    raise
                transport_attempt += 1
                delay = self._retry_delay(failure, attempt=transport_attempt)
                if delay is None:
                    raise
                if delay:
                    await self._sleep(delay)
                continue

            if self.max_validation_retries and isinstance(result, Mapping):
                validation_raw = dict(result)
                validation_raw.pop("_usage", None)
                try:
                    RealizationValidator.parse_and_validate(
                        context, validation_raw,
                        enforce_dialogue_plan=self.enforce_dialogue_plan,
                    )
                except RealizationError as exc:
                    if validation_attempt >= self.max_validation_retries:
                        self._last_structured_error_code = exc.code
                        raise StructuredRealizationProviderError(exc.code) from exc
                    validation_attempt += 1
                    delay = self._bounded_backoff(validation_attempt)
                    if delay:
                        await self._sleep(delay)
                    continue
            return result


def wrap_resilient_provider(provider: Any) -> Any:
    if isinstance(provider, ResilientProviderProxy):
        return provider
    if not callable(getattr(provider, "realize", None)):
        return provider
    if provider.descriptor.name == "nvidia_mistral_large_4":
        # Mistral may spend 20-60s in visible/reasoning output. Another
        # transparent transport/validation retry could multiply voice latency
        # or tie up the turn for minutes. Fail over to Groq instead.
        return ResilientProviderProxy(provider, max_retries=0, max_validation_retries=0,
                                      enforce_dialogue_plan=response_semantic_filter_enabled())
    # Fast structured providers retain one bounded repair attempt.
    return ResilientProviderProxy(provider, max_validation_retries=1,
                                  enforce_dialogue_plan=response_semantic_filter_enabled())

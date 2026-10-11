from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from amora.app import AmoraCore
from amora.cognition import CloudflareCognitiveProvider, OpenRouterCognitiveProvider
from amora.events import PrivacyScope
from amora.models import TaskClass
from amora.secrets import SecretStore
from amora.time import SimulatedClock


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 11, 21, 30, tzinfo=timezone.utc))


def _secret_store(tmp_path: Path, monkeypatch) -> SecretStore:
    monkeypatch.setenv("AMORA_SECRET_CLOUDFLARE_API_TOKEN", "cf-test-token")
    monkeypatch.setenv("AMORA_SECRET_CLOUDFLARE_ACCOUNT_ID", "cf-test-account")
    monkeypatch.setenv("AMORA_SECRET_OPENROUTER_API_KEY", "or-test-token")
    return SecretStore(tmp_path / "secrets")


def test_mesh_provider_descriptors_are_structured_and_zero_cost_scoped(tmp_path: Path, monkeypatch) -> None:
    secrets = _secret_store(tmp_path, monkeypatch)
    cloudflare = CloudflareCognitiveProvider(secrets)
    openrouter = OpenRouterCognitiveProvider(secrets)

    assert cloudflare.descriptor.available is True
    assert cloudflare.descriptor.supports_structured_output is True
    assert cloudflare.descriptor.metadata["unified_billing"] is False
    assert cloudflare.descriptor.model == "@cf/meta/llama-3.1-8b-instruct-fast"

    assert openrouter.descriptor.available is True
    assert openrouter.descriptor.supports_structured_output is True
    assert openrouter.descriptor.model == "openrouter/free"
    assert openrouter.descriptor.metadata["zero_cost_route"] is True


def test_openrouter_paid_route_is_fail_closed(tmp_path: Path, monkeypatch) -> None:
    secrets = _secret_store(tmp_path, monkeypatch)
    provider = OpenRouterCognitiveProvider(secrets, model="vendor/paid-model")
    assert provider.descriptor.available is False
    assert provider.descriptor.metadata["zero_cost_route"] is False


def test_cloudflare_is_not_registered_without_explicit_zero_cost_confirmation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.setenv("AMORA_SECRET_CLOUDFLARE_API_TOKEN", "cf-test-token")
    monkeypatch.setenv("AMORA_SECRET_CLOUDFLARE_ACCOUNT_ID", "cf-test-account")
    monkeypatch.delenv("AMORA_CLOUDFLARE_ZERO_COST_CONFIRMED", raising=False)
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        report = core.health_report()
        assert report["cloudflare_cognition_configured"] is True
        assert report["cloudflare_zero_cost_confirmed"] is False
        assert report["cloudflare_cognition_registered"] is False
        assert core.models.descriptor("cloudflare") is None
    finally:
        core.close()


def test_confirmed_zero_cost_mesh_registers_and_ranks_direct_provider_before_broker(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.setenv("AMORA_SECRET_CLOUDFLARE_API_TOKEN", "cf-test-token")
    monkeypatch.setenv("AMORA_SECRET_CLOUDFLARE_ACCOUNT_ID", "cf-test-account")
    monkeypatch.setenv("AMORA_CLOUDFLARE_ZERO_COST_CONFIRMED", "true")
    monkeypatch.setenv("AMORA_SECRET_OPENROUTER_API_KEY", "or-test-token")
    monkeypatch.setenv("AMORA_OPENROUTER_COGNITION_MODEL", "openrouter/free")
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        report = core.health_report()
        assert report["cloudflare_cognition_registered"] is True
        assert report["openrouter_cognition_registered"] is True
        candidates = core.cognition.router.ranked_candidates(
            TaskClass.SOCIAL_DIALOGUE,
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
            require_structured_output=True,
        )
        names = [item.name for item in candidates]
        assert "cloudflare" in names
        assert "openrouter" in names
        assert names.index("cloudflare") < names.index("openrouter")
    finally:
        core.close()


def test_mesh_privacy_gate_excludes_all_remote_providers_for_secret_scope(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.setenv("AMORA_SECRET_CLOUDFLARE_API_TOKEN", "cf-test-token")
    monkeypatch.setenv("AMORA_SECRET_CLOUDFLARE_ACCOUNT_ID", "cf-test-account")
    monkeypatch.setenv("AMORA_CLOUDFLARE_ZERO_COST_CONFIRMED", "true")
    monkeypatch.setenv("AMORA_SECRET_OPENROUTER_API_KEY", "or-test-token")
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        candidates = core.cognition.router.ranked_candidates(
            TaskClass.SOCIAL_DIALOGUE,
            privacy_scope=PrivacyScope.SECRET,
            remote_processing_allowed=True,
            require_structured_output=True,
        )
        assert candidates == ()
    finally:
        core.close()

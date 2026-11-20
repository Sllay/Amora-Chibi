"""Fail-closed staging selector contracts; no Aiven network or real secrets."""
from __future__ import annotations

import pytest

from amora.storage.backend import backend_from_environment
from amora.storage.postgres_runtime_connection import PostgresRuntimeError


def _settings(monkeypatch, tmp_path):
    cert = tmp_path / "ca.pem"
    cert.write_text(
        "-----BEGIN CERTIFICATE-----\nFICTIONAL-TEST-ONLY\n"
        "-----END CERTIFICATE-----\n", encoding="utf-8",
    )
    monkeypatch.setenv("AMORA_STORAGE_BACKEND", "postgres-staging")
    monkeypatch.setenv(
        "AMORA_POSTGRES_DSN",
        "postgresql://synthetic:fake-secret@pg-fixture.aivencloud.com:28450/defaultdb",
    )
    monkeypatch.setenv("AMORA_POSTGRES_CA_FILE", str(cert))
    monkeypatch.delenv("AMORA_POSTGRES_CA_PEM", raising=False)
    monkeypatch.setenv("AMORA_STAGING_EXPECTED_HOST", "pg-fixture.aivencloud.com")
    monkeypatch.setenv("AMORA_STAGING_EXPECTED_PORT", "28450")
    monkeypatch.setenv("AMORA_STAGING_EXPECTED_DATABASE", "defaultdb")
    monkeypatch.delenv("AMORA_PG_STAGING_APPROVAL", raising=False)


def test_postgres_staging_never_selected_without_explicit_approval(monkeypatch, tmp_path):
    _settings(monkeypatch, tmp_path)
    with pytest.raises(PostgresRuntimeError, match="PG_STAGING_OPERATOR_APPROVAL_REQUIRED"):
        backend_from_environment()
    monkeypatch.setenv("AMORA_STORAGE_BACKEND", "postgres")
    with pytest.raises(PostgresRuntimeError, match="PG_PRODUCTION_CUTOVER_APPROVAL_REQUIRED"):
        backend_from_environment()


def test_staging_backend_accepts_only_exact_tls_pinned_target(monkeypatch, tmp_path):
    _settings(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "AMORA_PG_STAGING_APPROVAL", "ALLOW_ONLY_ISOLATED_AIVEN_STAGING_CORE",
    )
    candidate = backend_from_environment()
    assert candidate.capabilities.name == "postgres-candidate"
    assert candidate.capabilities.remote is True
    assert candidate.opened_connections == 0  # no network call from configuration

    monkeypatch.setenv("AMORA_STAGING_EXPECTED_DATABASE", "otherdb")
    with pytest.raises(PostgresRuntimeError, match="PG_STAGING_ENDPOINT_PIN_MISMATCH"):
        backend_from_environment()
    monkeypatch.setenv("AMORA_STAGING_EXPECTED_DATABASE", "defaultdb")
    monkeypatch.setenv("AMORA_STAGING_EXPECTED_PORT", "0")
    with pytest.raises(PostgresRuntimeError, match="PG_STAGING_ENDPOINT_PIN_REQUIRED"):
        backend_from_environment()


def test_staging_missing_or_conflicting_ca_fails_without_secret_echo(monkeypatch, tmp_path):
    _settings(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "AMORA_PG_STAGING_APPROVAL", "ALLOW_ONLY_ISOLATED_AIVEN_STAGING_CORE",
    )
    monkeypatch.setenv("AMORA_POSTGRES_CA_FILE", "/not/mounted/ca.pem")
    with pytest.raises(PostgresRuntimeError) as caught:
        backend_from_environment()
    assert "fake-secret" not in str(caught.value)
    monkeypatch.setenv("AMORA_POSTGRES_CA_FILE", "")
    with pytest.raises(PostgresRuntimeError, match="PG_STAGING_MOUNTED_CA_REQUIRED"):
        backend_from_environment()
    monkeypatch.setenv("AMORA_POSTGRES_CA_PEM", "private secret must not be logged")
    with pytest.raises(PostgresRuntimeError, match="PG_STAGING_MOUNTED_CA_REQUIRED"):
        backend_from_environment()

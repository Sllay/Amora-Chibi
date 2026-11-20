"""Production-only Aiven selection and health must fail closed.

Unit tests use fake credentials and never access the real Aiven endpoint.
"""
from __future__ import annotations

from threading import RLock
from types import SimpleNamespace

import pytest

from amora.storage.backend import backend_from_environment
from amora.storage.database import AmoraDatabase
from amora.storage.postgres_runtime_connection import (
    PostgresRuntimeError, PostgresTargetPolicy,
)


def _configure(monkeypatch, tmp_path):
    cert = tmp_path / "ca.pem"
    cert.write_text(
        "-----BEGIN CERTIFICATE-----\nFICTIONAL-ONLY\n"
        "-----END CERTIFICATE-----\n", encoding="utf-8",
    )
    monkeypatch.setenv("AMORA_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("AMORA_MODE", "cloud")
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    monkeypatch.setenv(
        "AMORA_POSTGRES_DSN",
        "postgresql://fictional:fake-secret@pg-fixture.aivencloud.com:28450/defaultdb",
    )
    monkeypatch.setenv("AMORA_POSTGRES_CA_FILE", str(cert))
    monkeypatch.delenv("AMORA_POSTGRES_CA_PEM", raising=False)
    monkeypatch.setenv("AMORA_STAGING_EXPECTED_HOST", "pg-fixture.aivencloud.com")
    monkeypatch.setenv("AMORA_STAGING_EXPECTED_PORT", "28450")
    monkeypatch.setenv("AMORA_STAGING_EXPECTED_DATABASE", "defaultdb")
    monkeypatch.delenv("AMORA_PG_STAGING_APPROVAL", raising=False)
    monkeypatch.delenv("AMORA_PG_PRODUCTION_APPROVAL", raising=False)


def test_production_selector_requires_separate_explicit_approval(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    with pytest.raises(PostgresRuntimeError, match="PG_PRODUCTION_CUTOVER_APPROVAL_REQUIRED"):
        backend_from_environment()
    monkeypatch.setenv(
        "AMORA_PG_STAGING_APPROVAL", "ALLOW_ONLY_ISOLATED_AIVEN_STAGING_CORE",
    )
    with pytest.raises(PostgresRuntimeError, match="PG_PRODUCTION_CUTOVER_APPROVAL_REQUIRED"):
        backend_from_environment()
    monkeypatch.setenv(
        "AMORA_PG_PRODUCTION_APPROVAL", "AUTHORIZE_VERIFIED_AIVEN_PG18_CORE_CUTOVER",
    )
    with pytest.raises(PostgresRuntimeError, match="PG_PRODUCTION_STAGING_APPROVAL_CONFLICT"):
        backend_from_environment()
    monkeypatch.delenv("AMORA_PG_STAGING_APPROVAL", raising=False)
    candidate = backend_from_environment()
    assert candidate.capabilities.name == "postgres-candidate"
    assert candidate.capabilities.remote is True
    assert candidate.opened_connections == 0


def test_production_enforces_cloud_assets_endpoint_pin_and_ca(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "AMORA_PG_PRODUCTION_APPROVAL", "AUTHORIZE_VERIFIED_AIVEN_PG18_CORE_CUTOVER",
    )
    monkeypatch.setenv("AMORA_MODE", "local")
    with pytest.raises(PostgresRuntimeError, match="PG_PRODUCTION_CLOUD_MODE_REQUIRED"):
        backend_from_environment()
    monkeypatch.setenv("AMORA_MODE", "cloud")
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "files")
    with pytest.raises(PostgresRuntimeError, match="PG_PRODUCTION_AVATAR_DURABILITY_REQUIRED"):
        backend_from_environment()
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    monkeypatch.setenv("AMORA_STAGING_EXPECTED_DATABASE", "wrongdb")
    with pytest.raises(PostgresRuntimeError, match="PG_STAGING_ENDPOINT_PIN_MISMATCH"):
        backend_from_environment()
    monkeypatch.setenv("AMORA_STAGING_EXPECTED_DATABASE", "defaultdb")
    monkeypatch.setenv("AMORA_POSTGRES_CA_FILE", "/not/mounted/ca.pem")
    with pytest.raises(PostgresRuntimeError) as error:
        backend_from_environment()
    assert "fake-secret" not in str(error.value)


def test_production_readiness_recognizes_only_verified_aiven_pg18(monkeypatch):
    monkeypatch.setenv("AMORA_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv(
        "AMORA_PG_PRODUCTION_APPROVAL",
        "AUTHORIZE_VERIFIED_AIVEN_PG18_CORE_CUTOVER",
    )
    monkeypatch.delenv("AMORA_PG_STAGING_APPROVAL", raising=False)
    target = PostgresTargetPolicy(
        host="pg-fixture.aivencloud.com", port=28450,
        database="defaultdb", require_tls=True,
    )

    class ReadOnlyFakeConnection:
        _raw = object()
        _target = target

        def verify_prepared_schema(self, expected):
            assert expected == 18

    database = object.__new__(AmoraDatabase)
    database._lock = RLock()
    database.expected_schema_version = 18
    database.backend = SimpleNamespace(
        capabilities=SimpleNamespace(name="postgres-candidate"),
    )
    database._conn = ReadOnlyFakeConnection()
    inspected = []
    def verify(raw, policy, **kwargs):
        inspected.append((raw, policy, kwargs))
        return SimpleNamespace(status="structural_pass")
    monkeypatch.setattr(
        "amora.storage.postgres_run3_preflight.inspect_prepared_postgres",
        verify,
    )
    assert database.integrity_check() == "ok"
    assert len(inspected) == 1
    assert inspected[0][2]["require_memory_epoch"] is True
    assert inspected[0][2]["allow_empty_migrations_for_import"] is False
    monkeypatch.setenv("AMORA_PG_PRODUCTION_APPROVAL", "no")
    assert database.integrity_check() == "postgres-candidate-unverified"
    monkeypatch.setenv(
        "AMORA_PG_PRODUCTION_APPROVAL",
        "AUTHORIZE_VERIFIED_AIVEN_PG18_CORE_CUTOVER",
    )
    monkeypatch.setenv("AMORA_PG_STAGING_APPROVAL", "ALLOW_ONLY_ISOLATED_AIVEN_STAGING_CORE")
    assert database.integrity_check() == "postgres-candidate-unverified"

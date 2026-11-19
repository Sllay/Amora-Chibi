"""Read-only real Aiven Core continuity probe configuration tests.

Never contact Aiven, invoke Discord, or access real user data.
"""
from __future__ import annotations

import json

import pytest

from amora.storage import aiven_core_readonly_probe as probe


def _environment(monkeypatch, tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text(
        "-----BEGIN CERTIFICATE-----\nFICTIONAL-ONLY\n"
        "-----END CERTIFICATE-----\n", encoding="utf8",
    )
    monkeypatch.setenv(
        "AMORA_POSTGRES_DSN",
        "postgresql://fake-user:private-test-secret@fictional.aivencloud.com:28450/defaultdb",
    )
    monkeypatch.setenv("AMORA_POSTGRES_CA_FILE", str(ca))
    monkeypatch.delenv("AMORA_POSTGRES_CA_PEM", raising=False)
    monkeypatch.setenv("AMORA_STAGING_EXPECTED_HOST", "fictional.aivencloud.com")
    monkeypatch.setenv("AMORA_STAGING_EXPECTED_PORT", "28450")
    monkeypatch.setenv("AMORA_STAGING_EXPECTED_DATABASE", "defaultdb")
    return ca


def test_readonly_probe_requires_explicit_ca_mount_and_exact_pins(
    tmp_path, monkeypatch,
):
    ca = _environment(monkeypatch, tmp_path)
    dsn, path, target = probe._settings()
    assert path == str(ca)
    assert target.require_tls is True
    assert target.schema == "amora_pg_v18_lab"
    assert target.server_major == 18
    assert dsn.startswith("postgresql://")

    monkeypatch.setenv("AMORA_STAGING_EXPECTED_DATABASE", "otherdb")
    with pytest.raises(probe.AivenReadOnlyProbeError,
                       match="PG_READONLY_ENDPOINT_PIN_MISMATCH"):
        probe._settings()
    monkeypatch.setenv("AMORA_STAGING_EXPECTED_DATABASE", "defaultdb")
    monkeypatch.setenv("AMORA_POSTGRES_CA_FILE", "")
    with pytest.raises(probe.AivenReadOnlyProbeError,
                       match="PG_READONLY_CA_FILE_REQUIRED"):
        probe._settings()
    monkeypatch.setenv("AMORA_POSTGRES_CA_FILE", str(ca))
    monkeypatch.setenv("AMORA_POSTGRES_CA_PEM", "not allowed simultaneous source")
    with pytest.raises(probe.AivenReadOnlyProbeError,
                       match="PG_READONLY_CA_FILE_REQUIRED"):
        probe._settings()


def test_probe_error_report_never_echoes_credentials(tmp_path, monkeypatch, capsys):
    _environment(monkeypatch, tmp_path)
    monkeypatch.setenv("AMORA_STAGING_EXPECTED_PORT", "1")
    assert probe.main() == 2
    raw = capsys.readouterr().out
    assert raw.startswith("AIVEN_CORE_CONTINUITY ")
    info = json.loads(raw.split(" ", 1)[1])
    assert info["status"] == "blocked"
    assert info["reason"] == "PG_READONLY_ENDPOINT_PIN_MISMATCH"
    assert info["writes_performed"] is False
    assert info["production_cutover_authorized"] is False
    assert "private-test-secret" not in raw
    assert "fictional.aivencloud.com" not in raw


@pytest.mark.skipif(
    __import__("os").getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Disposable PostgreSQL 18 is required",
)
def test_readonly_probe_original_core_store_can_recall_imported_memory(
    tmp_path, monkeypatch,
):
    """Run the same Core query registry against isolated PG18 with invented data."""
    from contextlib import closing
    from datetime import datetime, timezone
    from pathlib import Path
    from uuid import UUID

    from amora.app import AmoraCore
    from amora.events import PrivacyScope
    from amora.memory.models import (
        MemoryKind, MemoryProvenance, MemoryRecord, ProvenanceKind,
    )
    from amora.storage.postgres_runtime_connection import PostgresTargetPolicy
    from amora.storage.postgres_runtime_registry import synthetic_core_backend
    from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
    from amora.time import SimulatedClock

    psycopg = pytest.importorskip("psycopg")

    def local():
        return psycopg.connect(
            host="127.0.0.1", port=5432, dbname="amora_pg_synthetic",
            user="amora_ci", password="synthetic_only",
            connect_timeout=5, autocommit=True,
        )

    def local_readonly():
        return psycopg.connect(
            host="127.0.0.1", port=5432, dbname="amora_pg_synthetic",
            user="amora_ci", password="synthetic_only",
            connect_timeout=5, autocommit=True,
            options="-c default_transaction_read_only=on -c search_path=pg_catalog",
        )

    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "synthetic-home"))
    monkeypatch.setenv("AMORA_MODE", "local")
    monkeypatch.setattr(
        probe, "_settings", lambda: ("", "", PostgresTargetPolicy()),
    )
    now = datetime(2026, 11, 17, tzinfo=timezone.utc)
    with closing(local()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            backend = synthetic_core_backend(local, max_connections=16)
            core = AmoraCore.bootstrap(
                clock=SimulatedClock(now), storage=backend,
            )
            try:
                historical = MemoryRecord(
                    id=UUID(int=2026111901),
                    kind=MemoryKind.EPISODIC,
                    content="lavandulaoito memoria velha inventada",
                    provenance=MemoryProvenance(
                        kind=ProvenanceKind.OBSERVATION,
                        source="synthetic-readonly-probe",
                        actor_id="PERSON_BK",
                    ),
                    privacy_scope=PrivacyScope.PRIVATE,
                    owner_entity_id="PERSON_BK",
                    remote_processing_allowed=False,
                    created_at=now, updated_at=now,
                    importance=0.9, gate_score=0.9,
                )
                assert core.memory.store.put(historical, insert_only=True) is True
            finally:
                core.close()
            assert backend.opened_connections == 0

            result = probe.inspect(connection_factory=local_readonly)
            assert result["status"] == "real_staging_read_only_core_continuity_pass"
            assert result["historical_memory_hydrated"] is True
            assert result["private_recall"] == (
                "owner_recall_allowed_other_principal_denied"
            )
            assert result["memories"] >= 1
            assert result["identities"] >= 1
            assert result["read_only_session"] is True
            assert result["writes_performed"] is False
            assert result["discord_connected"] is False
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")

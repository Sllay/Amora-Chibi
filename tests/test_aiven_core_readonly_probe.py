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

"""Synthetic offline regression tests: no live credentials, data or network."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("cryptography")
from amora.storage import pcs4c_drive_backup as backup


def _fixture(tmp_path):
    source = tmp_path / "amora-pre-pcs4c.db"
    sidecar = tmp_path / "amora-pre-pcs4c.db.avatar.zip"
    source.write_bytes(b"synthetic-pages-" * 400)
    sidecar.write_bytes(b"synthetic-sidecar")
    return source, sidecar


def test_gcm_backup_roundtrip_and_detect_tampering(tmp_path):
    source, sidecar = _fixture(tmp_path)
    cipher = tmp_path / "copy.agcm"
    password = "only-for-tests-not-a-real-password"
    expected = backup.encrypt_pair(source, sidecar, cipher, password)
    assert cipher.read_bytes().startswith(backup._MAGIC)
    assert source.read_bytes() not in cipher.read_bytes()
    paths = backup.restore_pair(cipher, tmp_path / "readback", password, verify_sqlite=False)
    assert set(paths) == set(backup._NAMES)
    for name, path in paths.items():
        assert backup._sha(path) == expected[name]

    altered = bytearray(cipher.read_bytes())
    altered[-20] ^= 0x20
    cipher.write_bytes(altered)
    with pytest.raises(backup.CloudBackupError, match="AUTHENTICATION_FAILED"):
        backup.restore_pair(cipher, tmp_path / "tampered", password, verify_sqlite=False)
    assert list((tmp_path / "tampered").iterdir()) == []


def test_wrong_password_fails(tmp_path):
    source, sidecar = _fixture(tmp_path)
    cipher = tmp_path / "copy.agcm"
    backup.encrypt_pair(source, sidecar, cipher, "a-correct-synthetic-password-123")
    with pytest.raises(backup.CloudBackupError, match="AUTHENTICATION_FAILED"):
        backup.restore_pair(
            cipher, tmp_path / "wrong", "a-different-synthetic-password-123",
            verify_sqlite=False,
        )


def test_weak_password_rejected_before_file_creation(tmp_path):
    source, sidecar = _fixture(tmp_path)
    cipher = tmp_path / "missing.agcm"
    with pytest.raises(backup.CloudBackupError, match="STRONG_PASSPHRASE_REQUIRED"):
        backup.encrypt_pair(source, sidecar, cipher, "tiny")
    assert not cipher.exists()


def test_default_inspect_is_offline(monkeypatch, capsys):
    monkeypatch.setattr(backup, "_source_inspect", lambda: {
        "logical_rows": 48059, "snapshot_bytes": 75563008,
    })
    assert backup.main(["inspect"]) == 0
    result = json.loads(capsys.readouterr().out.split(" ", 1)[1])
    assert result["network_access"] is False
    assert result["cloud_backup_performed"] is False


def test_backup_without_explicit_confirmation_cannot_execute(monkeypatch, capsys):
    monkeypatch.setattr(
        backup, "backup_to_drive",
        lambda *a, **kw: pytest.fail("unexpected transfer"),
    )
    assert backup.main(["backup"]) == 2
    report = json.loads(capsys.readouterr().out.split(" ", 1)[1])
    assert report["reason"] == "PCS4C_DRIVE_OPERATOR_CONFIRMATION_REQUIRED"


def test_api_host_is_pinned():
    with pytest.raises(backup.CloudBackupError, match="NETWORK_DESTINATION_REJECTED"):
        backup._http(
            "GET", "https://different.example.invalid/request",
            allow_host="www.googleapis.com",
        )

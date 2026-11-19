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


def test_downloaded_ciphertext_is_authenticated_and_restored_before_success(tmp_path, monkeypatch):
    source, sidecar = _fixture(tmp_path)
    cipher = tmp_path / "original.agcm"
    password = "test-long-random-backup-passphrase-123"
    hashes = backup.encrypt_pair(source, sidecar, cipher, password)
    size = cipher.stat().st_size
    with cipher.open("rb") as handle:
        import hashlib
        md5sum = hashlib.file_digest(handle, "md5").hexdigest()

    monkeypatch.setattr(
        backup, "verify_snapshot",
        lambda path, **kwargs: {"database_sha256": backup._sha(path)},
    )

    def fake_http(method, url, *, headers, allow_host, stream_to=None, **kwargs):
        if stream_to is None:
            return 200, {}, json.dumps({
                "id": "test-file-54321", "name": cipher.name,
                "size": str(size), "md5Checksum": md5sum,
            }).encode()
        assert method == "GET"
        assert url.endswith("?alt=media")
        stream_to.write_bytes(cipher.read_bytes())
        return 200, {}, b""

    backup._verify_remote(
        cipher, tmp_path / "download.agcm", "synthetic-grant", "test-file-54321",
        hashes, password, http=fake_http,
    )
    assert (tmp_path / "restored" / "amora-pre-pcs4c.db").read_bytes() == source.read_bytes()


def test_downloaded_ciphertext_changed_is_never_accepted(tmp_path):
    source, sidecar = _fixture(tmp_path)
    cipher = tmp_path / "original.agcm"
    password = "test-long-random-backup-passphrase-123"
    hashes = backup.encrypt_pair(source, sidecar, cipher, password)
    with cipher.open("rb") as handle:
        import hashlib
        md5sum = hashlib.file_digest(handle, "md5").hexdigest()

    def fake_http(method, url, *, headers, allow_host, stream_to=None, **kwargs):
        if stream_to is None:
            return 200, {}, json.dumps({
                "id": "test-file-54321", "name": cipher.name,
                "size": str(cipher.stat().st_size), "md5Checksum": md5sum,
            }).encode()
        stream_to.write_bytes(cipher.read_bytes()[:-1] + b"x")
        return 200, {}, b""

    with pytest.raises(
        backup.CloudBackupError, match="DOWNLOADED_CIPHERTEXT_MISMATCH",
    ):
        backup._verify_remote(
            cipher, tmp_path / "download.agcm", "synthetic-grant",
            "test-file-54321", hashes, password, http=fake_http,
        )
    assert not (tmp_path / "restored").exists()

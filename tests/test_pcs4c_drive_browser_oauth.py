"""Google consent and upload fakes: never connect to live Google."""
from __future__ import annotations

import json

from amora.storage import pcs4c_drive_backup as backup


def test_device_flow_pending_then_consent_without_logging_credentials():
    calls = []
    logs = []

    def http(method, url, *, body, headers, allow_host):
        calls.append((url, body))
        if url == backup._GOOGLE_DEVICE_URL:
            return 200, {}, json.dumps({
                "verification_url": "https://www.google.com/device",
                "device_code": "synthetic-device-check",
                "user_code": "ABCD-WXYZ",
                "expires_in": 300, "interval": 5,
            }).encode()
        if len(calls) == 2:
            return 400, {}, b'{"error":"authorization_pending"}'
        return 200, {}, json.dumps({
            "access_token": "synthetic-access-grant",
            "token_type": "Bearer",
            "scope": backup._DRIVE_SCOPE,
        }).encode()

    grant = backup._google_authenticate(
        "1234-testing.apps.googleusercontent.com",
        http=http,
        pause=lambda interval: None,
        monotonic=lambda: 0,
        notify=lambda line, **kw: logs.append(line),
    )
    assert grant == "synthetic-access-grant"
    assert len(calls) == 3
    assert b"drive.file" in calls[0][1]
    assert len(logs) == 1
    assert "ABCD-WXYZ" in logs[0]
    assert "synthetic-device-check" not in logs[0]
    assert "synthetic-access-grant" not in logs[0]


def test_drive_upload_uses_confirmed_chunks_without_redirects(tmp_path):
    cipher = tmp_path / "test.agcm"
    cipher.write_bytes(b"A" * (backup._CHUNK + 12))
    calls = []

    def http(method, url, *, body=None, headers=None, allow_host):
        calls.append((method, headers))
        if method == "POST" and url.startswith(backup._DRIVE_UPLOAD):
            return 200, {"Location": backup._DRIVE_UPLOAD + "?uploadType=resumable&upload_id=123"}, b""
        if method == "POST":
            return 200, {}, b'{"id":"synthetic-folder-12345"}'
        if method == "PUT" and len(calls) == 3:
            assert headers["Content-Range"] == f"bytes 0-{backup._CHUNK-1}/{backup._CHUNK+12}"
            return 308, {"Range": f"bytes=0-{backup._CHUNK-1}"}, b""
        if method == "PUT":
            assert headers["Content-Range"] == f"bytes {backup._CHUNK}-{backup._CHUNK+11}/{backup._CHUNK+12}"
            return 200, {}, b'{"id":"synthetic-file-12345"}'
        raise AssertionError("unsupported operation")

    folder, file_id = backup._upload(cipher, "synthetic-grant", http=http)
    assert (folder, file_id) == ("synthetic-folder-12345", "synthetic-file-12345")
    assert len(calls) == 4
    assert all(call[1]["Authorization"] == "Bearer synthetic-grant" for call in calls)


def test_google_device_url_must_be_official():
    import pytest
    with pytest.raises(backup.CloudBackupError, match="DEVICE_URL_INVALID"):
        backup._google_authenticate(
            "1234-testing.apps.googleusercontent.com",
            http=lambda *args, **kw: (
                200, {}, json.dumps({
                    "verification_url": "https://not-google.example",
                    "device_code": "synthetic",
                    "user_code": "ABCD-WXYZ",
                    "expires_in": 300, "interval": 5,
                }).encode(),
            ),
            notify=lambda *args, **kw: pytest.fail("should not display URL"),
        )

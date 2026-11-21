"""Altare API contracts with fake transport; never contact the provider."""
from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

# Core CI adds /core, not necessarily repository root, to the Python path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ops.altare_readonly_diagnostics import (
    AltareControlError, inspect, read_log_counts, sanitize_logs, _read,
)


def fake_fetch(path, token, *, params=None):
    assert token == "token-is-never-printed"
    if path == "/api-tokens/self":
        return {"team": {"id": "team_abc"}, "scopes": [
            "servers:view", "servers:console"]}
    if path == "/tenants/team_abc/servers":
        return {"items": [
            {"id": "app_core", "name": "Core-Amora",
             "memoryMb": 2048, "diskMb": 5000, "cpuPercent": 50},
            {"id": "app_discord", "name": "Discord-Amora",
             "memoryMb": 1024, "diskMb": 5000, "cpuPercent": 25},
        ]}
    if path.endswith("/logs"):
        assert params["limit"] == "1000"
        return {"lines": [
            {"text": "2026 INFO WEB_SCREENSHOT_UNAVAILABLE reason=provider_timeout SECRET=very-sensitive Discord token", "ts": "now"},
            {"text": "2026 INFO WEB_SCREENSHOT_UPLOAD_REJECTED status=400 Discord user: secret"},
            {"text": "2026 INFO WEB_SEARCH_CONTEXT_RESOLVED user said bad secret"},
            {"text": "general user message contains PRIVATE_TOKEN_VERY_SECRET"},
        ], "next": None}
    raise AssertionError(path)


def test_read_only_preflight_does_not_emit_account_credentials():
    info = inspect("token-is-never-printed", fetch=fake_fetch)
    assert info["status"] == "ready"
    assert info["token_can_read_logs"]
    assert info["apps"]["Core-Amora"]["memory_mb"] == 2048
    assert "team_abc" not in str(info)
    assert "token-is-never-printed" not in str(info)


def test_allowlisted_event_counts_never_copy_logs_users_or_tokens():
    result = read_log_counts("token-is-never-printed", app="Discord-Amora",
                             fetch=fake_fetch, hours=24)
    encoded = json.dumps(result)
    assert result["events"]["WEB_SCREENSHOT_UNAVAILABLE"] == 1
    assert result["events"]["WEB_SCREENSHOT_UPLOAD_REJECTED"] == 1
    assert result["screenshot_reasons"]["provider_timeout"] == 1
    assert result["lines_scanned"] == 4
    assert "PRIVATE_TOKEN" not in encoded
    assert "SECRET=" not in encoded
    assert "Discord user" not in encoded


def test_missing_app_or_permissions_blocks_without_exposing_token():
    def missing(path, token, **kwargs):
        result = fake_fetch(path, token, **kwargs)
        if path.endswith("/servers"):
            return {"items": []}
        return result
    assert inspect("token-is-never-printed", fetch=missing)["status"] == (
        "apps_not_created"
    )
    def forbidden(path, token, **kwargs):
        if path == "/api-tokens/self":
            return {"team": {"id": "team_abc"}, "scopes": []}
        return fake_fetch(path, token, **kwargs)
    with pytest.raises(AltareControlError, match="SERVERS_VIEW"):
        inspect("token-is-never-printed", fetch=forbidden)


def test_api_reader_rejects_uncertain_path_without_network():
    with pytest.raises(AltareControlError, match="API_PATH"):
        _read("/../steal", "token-is-never-printed",
              request=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                  AssertionError("must not connect")
              ))


def test_malformed_logs_never_escape_as_raw_message():
    with pytest.raises(AltareControlError, match="LOG_FORMAT"):
        sanitize_logs({"lines": "private message"})


def test_403_html_error_is_identified_without_leaking_raw_edge_content():
    private_marker = "PRIVATE_HEADER_AND_TOKEN"
    def denied(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://console.altare.gg/api/api-tokens/self",
            403, "Forbidden",
            {"Content-Type": "text/html", "Server": "cloudflare"},
            io.BytesIO(("<html>" + private_marker + "</html>").encode()),
        )
    with pytest.raises(AltareControlError, match="ALTARE_API_HTTP_403_HTML") as raised:
        _read("/api-tokens/self", "token-is-never-printed", request=denied)
    assert private_marker not in str(raised.value)
    assert "token-is-never-printed" not in str(raised.value)


def test_api_json_auth_failure_preserves_only_allowlisted_error_codes():
    def denied(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://console.altare.gg/api/api-tokens/self",
            401, "Unauthorized", {"Content-Type": "application/json"},
            io.BytesIO(b'{"code":"TOKEN_REVOKED","message":"PRIVATE_ERROR_BODY"}'),
        )
    with pytest.raises(AltareControlError, match="ALTARE_API_HTTP_401_JSON_TOKEN_REVOKED") as raised:
        _read("/api-tokens/self", "token-is-never-printed", request=denied)
    assert "PRIVATE_ERROR_BODY" not in str(raised.value)

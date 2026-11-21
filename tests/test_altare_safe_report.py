"""GitHub issue diagnostics must publish codes, not sensitive log bodies."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.ops.altare_safe_report import ISSUE_URL, publish, safe_report


class _Response:
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return None
    def read(self, n):
        return b"ok"


def test_exact_code_allowlist_and_owner_issue_report():
    raw = [
        {"status": "ready", "apps": {
            "amora-core": {"exists": True, "memory_mb": "PRIVATE_PWD"},
            "amora-discord": {"exists": False},
            "evil-secret-app": {"exists": True},
        }},
        {"app": "amora-core", "events": {
            "WEB_SCREENSHOT_UNAVAILABLE": 2,
            "WEB_WORKSPACE_READ_FAILED": 1,
            "private user said password": 10,
        }, "screenshot_reasons": {
            "provider_timeout": 1,
            "password=secret": 99,
        }, "raw_logs": "raw secret screenshot data"},
        {"status": "blocked", "reason": "SECRET_TOKEN_PLEASE_LEAK"},
    ]
    source = "\n".join(json.dumps(x) for x in raw)
    result = safe_report(source)
    assert "WEB_SCREENSHOT_UNAVAILABLE: 2" in result
    assert "WEB_WORKSPACE_READ_FAILED: 1" in result
    assert "provider_timeout: 1" in result
    assert "ALTARE_UNAVAILABLE" in result
    assert "amora-core: encontrado" in result
    assert "amora-discord: nao encontrado" in result
    assert "PRIVATE_PWD" not in result
    assert "evil-secret-app" not in result
    assert "SECRET_TOKEN" not in result
    assert "raw secret" not in result
    assert "password" not in result


def test_issue_comment_contains_only_sanitized_body_and_bearer_is_not_log():
    seen = []
    def opener(request, timeout):
        seen.append((request, timeout))
        return _Response()
    source = json.dumps({
        "status": "blocked", "reason": "ALTARE_LOG_SCOPE_REQUIRED",
        "private": "DISCORD_BOT_TOKEN=TOP_SECRET",
    })
    publish(source, "private-github-token", opener=opener)
    assert len(seen) == 1
    req, timeout = seen[0]
    assert req.full_url == ISSUE_URL
    assert req.get_method() == "POST"
    body = json.loads(req.data.decode())
    assert body == {"body": safe_report(source)}
    assert "private-github-token" not in str(body)
    assert "TOP_SECRET" not in str(body)
    assert timeout == 12


@pytest.mark.parametrize("bad", [
    "", "line not JSON", '[{"malicious":"list"}]',
    '{"status": "blocked", "reason": "PASSWORD_PLEASE"}',
])
def test_malformed_reports_never_echo_input(bad):
    output = safe_report(bad)
    assert "PASSWORD_PLEASE" not in output
    assert "malicious" not in output
    assert "line not JSON" not in output


def test_invalid_github_token_fails_before_connect():
    with pytest.raises(ValueError, match="TOKEN_INVALID"):
        publish('{}', "token with whitespace",
                opener=lambda *args, **kwargs: (_ for _ in ()).throw(
                    AssertionError("must not connect")
                ))

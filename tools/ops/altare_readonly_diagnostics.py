"""Read-only Altare staging preflight and allowlisted log diagnostics.

No install, deploy, writes, raw log output or secret exports.
Only secrets read: ALTARE_TOKEN from CI environment; its value is NEVER logged.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

BASE = "https://console.altare.gg/api"
EVENTS = frozenset({
    "WEB_SCREENSHOT_UNAVAILABLE",
    "WEB_SCREENSHOT_DISPATCH",
    "WEB_SCREENSHOT_UPLOAD_REJECTED",
    "WEB_SCREENSHOT_ATTACHMENT_CONFIRMED",
    "WEB_SEARCH_CONTEXT_MISSING",
    "WEB_SEARCH_CONTEXT_RESOLVED",
    "WEB_WORKSPACE_PERSIST_FAILED",
    "WEB_WORKSPACE_RESTORE_FAILED",
    "WEB_WORKSPACE_READ_FAILED",
    "WEB_WORKSPACE_ACTION_FAILED",
    "ENTRYPOINT_FATAL",
    "PROCESS_EXIT",
    "READY",
})
REASONS = frozenset({
    "provider_rate_limited", "provider_auth_required", "provider_http_error",
    "provider_refused", "provider_response_invalid", "provider_timeout",
    "provider_network_error", "provider_image_url_invalid",
    "image_cdn_http_error", "image_mime_invalid", "image_bytes_invalid",
    "daily_limit", "disabled", "invalid_public_url",
    "no_authorized_public_page", "provider_unknown",
})
EVENT_RE = re.compile(r"\b(" + "|".join(sorted(EVENTS)) + r")\b")
REASON_RE = re.compile(r"\breason=(" + "|".join(sorted(REASONS)) + r")\b")
NAME_RE = re.compile(r"[a-zA-Z0-9_-]{2,70}\Z")


class AltareControlError(RuntimeError):
    pass


def _read(path: str, token: str, *, params: dict[str, str] | None = None,
          request: Callable[..., Any] = urllib.request.urlopen) -> dict[str, Any]:
    if not token or len(token) > 512 or "\n" in token:
        raise AltareControlError("ALTARE_TOKEN_MISSING_OR_INVALID")
    if not path.startswith("/") or ".." in path:
        raise AltareControlError("ALTARE_API_PATH_INVALID")
    query = "?" + urllib.parse.urlencode(params) if params else ""
    req = urllib.request.Request(
        BASE + path + query,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/json"},
        method="GET",
    )
    try:
        with request(req, timeout=12) as response:
            content = response.read(2_000_001)
        if len(content) > 2_000_000:
            raise AltareControlError("ALTARE_RESPONSE_TOO_LARGE")
        obj = json.loads(content.decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, UnicodeError):
        raise AltareControlError("ALTARE_READONLY_API_UNAVAILABLE") from None
    if not isinstance(obj, dict):
        raise AltareControlError("ALTARE_API_RESPONSE_INVALID")
    return obj


def inspect(token: str, names: tuple[str, ...] = ("amora-core", "amora-discord"),
            *, fetch: Callable[..., dict[str, Any]] = _read) -> dict[str, Any]:
    if any(not NAME_RE.fullmatch(name) for name in names):
        raise AltareControlError("ALTARE_APP_NAME_INVALID")
    identity = fetch("/api-tokens/self", token)
    team = identity.get("team") or {}
    team_id = team.get("id")
    scopes = set(identity.get("scopes") or [])
    if not isinstance(team_id, str) or not NAME_RE.fullmatch(team_id):
        raise AltareControlError("ALTARE_TEAM_ID_UNAVAILABLE")
    if "servers:view" not in scopes:
        raise AltareControlError("ALTARE_SCOPE_SERVERS_VIEW_REQUIRED")
    apps = fetch(f"/tenants/{team_id}/servers", token)
    listed = {item.get("name"): item for item in apps.get("items", [])
              if isinstance(item, dict)}
    statuses: dict[str, Any] = {}
    for name in names:
        app = listed.get(name)
        statuses[name] = {
            "exists": bool(app),
            "memory_mb": app.get("memoryMb") if app else None,
            "cpu_pct": app.get("cpuPercent") if app else None,
            "disk_mb": app.get("diskMb") if app else None,
        }
    # No token name, ID, prefix or team ID printed to public CI logs.
    return {
        "status": "ready" if all(v["exists"] for v in statuses.values())
                  else "apps_not_created",
        "token_can_read_logs": "servers:console" in scopes,
        "apps": statuses,
    }


def sanitize_logs(payload: dict[str, Any]) -> dict[str, Any]:
    """Only exact allowlisted codes and counts leave the ephemeral runner."""
    counters: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    lines = payload.get("lines") or []
    if not isinstance(lines, list):
        raise AltareControlError("ALTARE_LOG_FORMAT_INVALID")
    for item in lines[:1000]:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("text") or "")[:700]
        for match in EVENT_RE.finditer(raw):
            counters[match.group(1)] += 1
        reason = REASON_RE.search(raw)
        if reason and ("WEB_SCREENSHOT_UNAVAILABLE" in raw):
            reasons[reason.group(1)] += 1
    return {"events": dict(sorted(counters.items())),
            "screenshot_reasons": dict(sorted(reasons.items())),
            "lines_scanned": min(len(lines), 1000)}


def read_log_counts(token: str, *, app: str, fetch: Callable[..., dict[str, Any]] = _read,
                    hours: int = 24) -> dict[str, Any]:
    if not NAME_RE.fullmatch(app) or not 1 <= hours <= 72:
        raise AltareControlError("ALTARE_LOG_REQUEST_INVALID")
    identity = fetch("/api-tokens/self", token)
    scopes = set(identity.get("scopes") or [])
    team_id = (identity.get("team") or {}).get("id", "")
    if "servers:console" not in scopes or "servers:view" not in scopes:
        raise AltareControlError("ALTARE_LOG_SCOPE_REQUIRED")
    if not NAME_RE.fullmatch(str(team_id)):
        raise AltareControlError("ALTARE_TEAM_ID_UNAVAILABLE")
    apps = fetch(f"/tenants/{team_id}/servers", token)
    matched = [entry for entry in apps.get("items", [])
               if isinstance(entry, dict) and entry.get("name") == app]
    if len(matched) != 1 or not NAME_RE.fullmatch(str(matched[0].get("id") or "")):
        raise AltareControlError("ALTARE_APP_NOT_FOUND")
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    data = fetch(f"/tenants/{team_id}/servers/{matched[0]['id']}/logs",
                 token, params={"since": since, "limit": "1000"})
    return {"app": app, "hours": hours, **sanitize_logs(data)}


def main() -> int:
    token = os.environ.get("ALTARE_TOKEN", "")
    try:
        report = inspect(token)
        print(json.dumps(report, sort_keys=True))
        if os.environ.get("ALTARE_LOG_COUNTS_ENABLED") == "true":
            if not report["token_can_read_logs"]:
                raise AltareControlError("ALTARE_LOG_SCOPE_REQUIRED")
            for app, data in report["apps"].items():
                if data["exists"]:
                    print(json.dumps(read_log_counts(token, app=app), sort_keys=True))
        return 0 if report["status"] == "ready" else 2
    except AltareControlError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

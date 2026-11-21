"""Post allowlisted Altare diagnostic counters to control issue #349."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable
import urllib.request

from .altare_readonly_diagnostics import EVENTS, REASONS

ALLOWED_BLOCKED = frozenset({
    "ALTARE_TOKEN_MISSING_OR_INVALID", "ALTARE_API_PATH_INVALID",
    "ALTARE_READONLY_API_UNAVAILABLE", "ALTARE_RESPONSE_TOO_LARGE",
    "ALTARE_API_RESPONSE_INVALID", "ALTARE_TEAM_ID_UNAVAILABLE",
    "ALTARE_SCOPE_SERVERS_VIEW_REQUIRED", "ALTARE_APP_NAME_INVALID",
    "ALTARE_LOG_SCOPE_REQUIRED", "ALTARE_APP_NOT_FOUND",
    "ALTARE_LOG_REQUEST_INVALID", "ALTARE_LOG_FORMAT_INVALID",
})
ALLOWED_APPS = frozenset({"amora-core", "amora-discord"})
ISSUE_URL = "https://api.github.com/repos/Sllay/Amora-Chibi/issues/349/comments"


def safe_report(source: str) -> str:
    """Rebuild from exact enums; never relay provider-controlled free text."""
    records: list[dict[str, Any]] = []
    try:
        for line in source.splitlines()[:8]:
            obj = json.loads(line)
            if isinstance(obj, dict):
                records.append(obj)
    except (ValueError, TypeError):
        return "Altare: diagnostico indisponivel (formato invalido)."
    messages = ["**Altare - diagnostico seguro** (somente codigos e contagens)"]
    for record in records[:4]:
        state = record.get("status")
        if state == "blocked":
            reason = record.get("reason")
            messages.append("Preflight bloqueado: " +
                            (reason if reason in ALLOWED_BLOCKED
                             else "ALTARE_UNAVAILABLE"))
        elif state in {"ready", "apps_not_created"}:
            messages.append("Preflight: " + state)
            apps = record.get("apps") or {}
            if isinstance(apps, dict):
                for name in sorted(ALLOWED_APPS):
                    details = apps.get(name)
                    if isinstance(details, dict):
                        messages.append(name + ": " + (
                            "encontrado" if details.get("exists") is True
                            else "nao encontrado"
                        ))
        elif record.get("app") in ALLOWED_APPS:
            messages.append("Eventos em " + record["app"] + ":")
            events = record.get("events") or {}
            if isinstance(events, dict):
                for event in sorted(EVENTS):
                    count = events.get(event)
                    if type(count) is int and 0 < count <= 1000:
                        messages.append("- " + event + ": " + str(count))
            reasons = record.get("screenshot_reasons") or {}
            if isinstance(reasons, dict):
                for reason in sorted(REASONS):
                    count = reasons.get(reason)
                    if type(count) is int and 0 < count <= 1000:
                        messages.append("- " + reason + ": " + str(count))
    messages.append("Nenhum log bruto, URL de conversa ou credencial publicado.")
    return "\n".join(messages)[:3600]


def publish(report: str, token: str, *,
            opener: Callable[..., Any] = urllib.request.urlopen) -> None:
    if not token or len(token) > 512 or any(c.isspace() for c in token):
        raise ValueError("ALTARE_ISSUE_TOKEN_INVALID")
    payload = json.dumps({"body": safe_report(report)}, ensure_ascii=False).encode()
    req = urllib.request.Request(
        ISSUE_URL, data=payload,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        }, method="POST",
    )
    try:
        with opener(req, timeout=12) as response:
            response.read(256)
    except Exception:
        raise RuntimeError("ALTARE_SAFE_REPORT_POST_FAILED") from None


def main() -> int:
    path = os.environ.get("ALTARE_DIAG_REPORT_PATH", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    try:
        content = Path(path).read_text(encoding="utf-8") if path else ""
        publish(content, token)
    except (OSError, ValueError, RuntimeError):
        print("ALTARE_SAFE_REPORT_NOT_POSTED")
        return 2
    print("ALTARE_SAFE_REPORT_POSTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

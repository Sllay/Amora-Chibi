"""Read-only Altare Core HTTPS origin inspection using a private team token.

Never prints tokens, API response bodies, app IDs or configuration secrets.
Only calls documented GET API endpoints; does not create domains or start apps.
"""
from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler


API = "https://console.altare.gg/api"
CORE = "Core-Amora"
DISCORD = "Discord-Amora"
EXPECTED = {"Core-Amora": "fi-hel03.altare.global:25193",
            "Discord-Amora": "fi-hel03.altare.global:25847"}
HOSTNAME = re.compile(r"[a-z0-9-]+\.go\.altare\.global\Z")


class SafeProbeError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api_get(path: str, token: str) -> dict:
    # Path segments are only API-supplied UUID-like IDs, never URLs or input.
    if not re.fullmatch(r"/[A-Za-z0-9/_-]+", path):
        raise SafeProbeError("INVALID_API_PATH")
    req = Request(API + path, headers={"Authorization": "Bearer " + token,
                                       "Accept": "application/json"})
    try:
        with build_opener(NoRedirect()).open(req, timeout=10) as response:
            body = response.read(128 * 1024 + 1)
            if len(body) > 128 * 1024:
                raise SafeProbeError("API_RESPONSE_TOO_LARGE")
            val = json.loads(body)
            if not isinstance(val, dict):
                raise SafeProbeError("API_RESPONSE_INVALID")
            return val
    except HTTPError as e:
        if e.code == 401:
            raise SafeProbeError("ALTARE_TOKEN_UNAUTHORIZED") from None
        if e.code == 403:
            raise SafeProbeError("ALTARE_SCOPE_DENIED") from None
        raise SafeProbeError("ALTARE_API_HTTP_FAILURE") from None
    except (URLError, ValueError, TimeoutError):
        raise SafeProbeError("ALTARE_API_UNAVAILABLE") from None


def https_domains(payload: dict) -> list[str]:
    out = []
    for item in payload.get("items", []):
        if not isinstance(item, dict):
            continue
        host = item.get("hostname", "")
        if (item.get("kind") == "https" and item.get("status") == "active"
                and isinstance(host, str) and HOSTNAME.fullmatch(host)):
            out.append("https://" + host)
    return sorted(set(out))


def inspect(token: str) -> list[str]:
    if not token.startswith("alt_"):
        raise SafeProbeError("ALTARE_TOKEN_MISSING")
    ident = api_get("/api-tokens/self", token)
    team = ident.get("team", {})
    team_id = team.get("id") if isinstance(team, dict) else None
    if not isinstance(team_id, str) or not re.fullmatch(r"[A-Za-z0-9-]+", team_id):
        raise SafeProbeError("ALTARE_TEAM_ID_UNAVAILABLE")
    scopes = ident.get("scopes") or []
    apps = api_get(f"/tenants/{team_id}/servers", token).get("items", [])
    if not isinstance(apps, list):
        raise SafeProbeError("ALTARE_APP_LIST_INVALID")
    summary = []
    for name in (CORE, DISCORD):
        matches = [x for x in apps if isinstance(x, dict) and x.get("name") == name]
        if len(matches) != 1:
            summary.append(name.upper() + "_NOT_VISIBLE")
            continue
        app_id = matches[0].get("id")
        if not isinstance(app_id, str) or not re.fullmatch(r"[A-Za-z0-9-]+", app_id):
            summary.append(name.upper() + "_ID_UNAVAILABLE")
            continue
        details = api_get(f"/tenants/{team_id}/servers/{app_id}/details", token)
        raw = details.get("ip", "")
        summary.append(name.upper() + "_RAW_PORT=" +
                       ("MATCHED" if raw == EXPECTED[name] else "DIFFERENT"))
        if name != CORE:
            continue
        if "servers:network" not in scopes:
            summary.append("CORE_HTTPS_DOMAIN=NOT_INSPECTABLE_SCOPE_MISSING")
            continue
        try:
            domains = api_get(
                f"/tenants/{team_id}/servers/{app_id}/network/domains", token
            )
        except SafeProbeError:
            summary.append("CORE_HTTPS_DOMAIN=NOT_INSPECTABLE")
            continue
        listed = https_domains(domains)
        if listed:
            summary.extend("CORE_HTTPS_ORIGIN=" + url for url in listed)
        else:
            summary.append("CORE_HTTPS_DOMAIN=NO_ACTIVE_DOMAIN")
    return summary


def main() -> int:
    try:
        for line in inspect(os.environ.get("ALTARE_TOKEN_CONFIG", "")):
            print(line)
        return 0
    except SafeProbeError as e:
        print(str(e))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

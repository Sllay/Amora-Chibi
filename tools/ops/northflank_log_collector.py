#!/usr/bin/env python3
"""Bounded, read-only Northflank log snapshots for a PRIVATE Git repository.

This program never writes to the public source repository or prints log text.
Only the workflow may decide where to publish its output. Python stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

API = "https://api.northflank.com"
KEEP = 6
LINE_LIMIT = 600
MAX_RESPONSE_BYTES = 2_000_000
MAX_SNAPSHOT_BYTES = 128_000
MAX_RESOURCES = 60

SENSITIVE_LINE = re.compile(
    r"(?i)(?:\b(?:authorization|bearer|password|passwd|api[_-]?key|"
    r"private[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"discord_bot_token|db_url|database_url|connection_string|"
    r"client_secret|cookie|set-cookie|jwt|webhook_secret)\b"
    r"[\s\"']*[:=])|(?:\b(?:gh[pousr]_|github_pat_|sk-)[A-Za-z0-9_-]{12,})"
)
URL = re.compile(r"https?://[^\s\"'<>()]+", re.IGNORECASE)
EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
DISCORD_ID = re.compile(r"\b\d{17,20}\b")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class CollectionError(Exception):
    """Sanitized collector error; never includes URL, tokens or log text."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CollectionError("api_redirect_denied")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def component(value: object) -> str:
    raw = str(value or "unnamed")
    label = re.sub(r"[^A-Za-z0-9_.-]", "-", raw).strip(".-")[:65] or "resource"
    return label + "-" + hashlib.sha256(raw.encode()).hexdigest()[:8]


def safe_log(value: object) -> str:
    """Conservative best-effort redaction. PRIVATE storage is still mandatory."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if SENSITIVE_LINE.search(text):
        return "[SENSITIVE_LINE_REDACTED]"
    text = ANSI.sub("", text)
    text = URL.sub("[URL_REDACTED]", text)
    text = EMAIL.sub("[EMAIL_REDACTED]", text)
    text = IPV4.sub("[IP_REDACTED]", text)
    text = DISCORD_ID.sub("[DISCORD_ID_REDACTED]", text)
    return text.replace("\r", "\\r").replace("\n", "\\n")[:3000]


def safe_id(value: object) -> str:
    value = str(value or "")
    if not value or len(value) > 128 or not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise CollectionError("invalid_resource_identifier")
    return value


class Northflank:
    def __init__(self, token: str, project: str, team: str = ""):
        if not token:
            raise CollectionError("missing_northflank_token")
        self.project = safe_id(project)
        self.team = safe_id(team) if team else ""
        self.token = token
        self.http = build_opener(NoRedirect())

    def get(self, suffix: str, query: dict | None = None):
        prefix = ("/v1/teams/" + quote(self.team, safe="") if self.team else "/v1")
        path = prefix + "/projects/" + quote(self.project, safe="") + suffix
        url = API + path + ("?" + urlencode(query) if query else "")
        request = Request(url, headers={
            "Authorization": "Bearer " + self.token,
            "Accept": "application/json",
            "User-Agent": "AmoraOpsLogs/1.0",
        })
        try:
            with self.http.open(request, timeout=16) as response:
                if response.status != 200:
                    raise CollectionError("northflank_http_" + str(response.status))
                payload = response.read(MAX_RESPONSE_BYTES + 1)
                if len(payload) > MAX_RESPONSE_BYTES:
                    raise CollectionError("northflank_response_too_large")
                doc = json.loads(payload)
        except HTTPError as error:
            raise CollectionError("northflank_http_" + str(error.code)) from None
        except (URLError, TimeoutError, OSError):
            raise CollectionError("northflank_network_error") from None
        except (ValueError, TypeError):
            raise CollectionError("northflank_invalid_json") from None
        if not isinstance(doc, dict) or "data" not in doc:
            raise CollectionError("northflank_invalid_response")
        return doc["data"]

    def list_resources(self, kind: str):
        plural = "services" if kind == "services" else "jobs"
        resources = []
        page = 1
        for _ in range(10):
            data = self.get("/" + plural, {"per_page": 100, "page": page})
            if not isinstance(data, dict) or not isinstance(data.get(plural), list):
                raise CollectionError("invalid_" + plural + "_list")
            resources.extend(data[plural])
            if len(resources) > MAX_RESOURCES:
                raise CollectionError("too_many_" + plural)
            if len(data[plural]) < 100:
                break
            page += 1
        else:
            raise CollectionError("resource_pagination_limit")
        return resources

    def versions(self, kind: str, resource_id: str):
        route = "/" + kind + "/" + quote(resource_id, safe="")
        listing = "deployments" if kind == "services" else "runs"
        data = self.get(route + "/" + listing, {"per_page": KEEP, "page": 1})
        rows = data.get(listing) if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise CollectionError("invalid_" + listing + "_list")
        return rows[:KEEP]

    def logs(self, kind: str, resource_id: str, version_id: str):
        selector = "deploymentId" if kind == "services" else "runId"
        query = {
            selector: version_id,
            "type": "runtime",
            "queryType": "range",
            "lineLimit": LINE_LIMIT,
            "direction": "backward",
        }
        data = self.get("/" + kind + "/" + quote(resource_id, safe="") + "/logs", query)
        if not isinstance(data, list):
            raise CollectionError("invalid_log_response")
        return data


def snapshot_text(rows: list, label: str, version: str, time: str) -> str:
    lines = [
        "# Sanitized, bounded Northflank runtime log excerpt",
        "# resource=" + label + " version=" + version,
        "# collected_at=" + time + " max_lines=" + str(LINE_LIMIT),
        "# Limited to the newest lines available from the API. Not a full deploy export.",
    ]
    if not rows:
        lines.append("# NO_LOG_LINES_RETURNED (the provider may no longer retain this run)")
    else:
        for row in sorted(rows[-LINE_LIMIT:], key=lambda x: str(x.get("ts", "")) if isinstance(x, dict) else ""):
            if not isinstance(row, dict):
                continue
            timestamp = re.sub(r"[^0-9TZ:.\-+]", "", str(row.get("ts") or ""))[:48]
            value = safe_log(row.get("log", ""))
            lines.append(timestamp + " " + value)
    result = "\n".join(lines) + "\n"
    encoded = result.encode("utf-8")
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        header = "\n# TRUNCATED_TO_LAST_128KB\n"
        size = MAX_SNAPSHOT_BYTES - len(header.encode())
        result = header + encoded[-size:].decode("utf-8", errors="replace")
    return result


def save_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def collect(api: Northflank, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    collected = utc_now()
    results = {"collected_at": collected, "project": api.project, "services": [], "jobs": []}
    total_errors = 0

    for kind in ("services", "jobs"):
        resources = api.list_resources(kind)
        for resource in resources:
            if not isinstance(resource, dict):
                raise CollectionError("invalid_resource_entry")
            resource_id = safe_id(resource.get("id"))
            name = str(resource.get("name") or resource_id)[:80]
            folder = output / kind / component(name) / component(resource_id)
            folder.mkdir(parents=True, exist_ok=True)
            try:
                versions = api.versions(kind, resource_id)
            except CollectionError as error:
                total_errors += 1
                results[kind].append({"id": resource_id, "name": name, "error": str(error)})
                # Preserve the last successful snapshot if history API fails.
                continue
            keep = set()
            entries = []
            for row in versions:
                if not isinstance(row, dict):
                    raise CollectionError("invalid_version_entry")
                version = safe_id(row.get("id"))
                filename = component(version) + ".log"
                keep.add(filename)
                meta = {
                    "id": version,
                    "status": str(row.get("status") or "unknown")[:24] if kind == "jobs" else (
                        "active" if row.get("active") else "previous"
                    ),
                    "started_at": str(row.get("startedAt") or row.get("createdAt") or "")[:50],
                    "file": filename,
                }
                if kind == "services" and isinstance(row.get("commit"), dict):
                    sha = str(row["commit"].get("sha") or "")
                    if re.fullmatch(r"[0-9a-fA-F]{7,64}", sha):
                        meta["commit"] = sha
                try:
                    logs = api.logs(kind, resource_id, version)
                    text = snapshot_text(logs, kind + "/" + resource_id, version, collected)
                    (folder / filename).write_text(text, encoding="utf-8")
                    meta["lines_returned"] = len(logs)
                    meta["collection_status"] = "ok" if logs else "empty"
                except CollectionError as error:
                    total_errors += 1
                    meta["collection_status"] = "failed"
                    meta["error"] = str(error)
                    # Leave the previously archived file intact, if present.
                entries.append(meta)
            for old in folder.glob("*.log"):
                if old.name not in keep:
                    old.unlink()
            save_json(folder / "index.json", {
                "kind": kind, "id": resource_id, "name": name,
                "updated_at": collected, "versions": entries, "retained_limit": KEEP,
            })
            results[kind].append({
                "id": resource_id, "name": name, "version_count": len(entries),
                "errors": sum(1 for item in entries if item["collection_status"] == "failed"),
            })
    save_json(output / "index.json", results)
    results["error_count"] = total_errors
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="Path in a PRIVATE checked-out repo")
    args = parser.parse_args()
    try:
        token = os.environ.get("NORTHFLANK_API_TOKEN", "")
        project = os.environ.get("NORTHFLANK_PROJECT_ID", "")
        team = os.environ.get("NORTHFLANK_TEAM_ID", "")
        destination = Path(args.output)
        if not destination.is_absolute():
            raise CollectionError("output_must_be_absolute")
        if not project:
            raise CollectionError("missing_northflank_project")
        result = collect(Northflank(token, project, team), destination)
        print("northflank_archive resources=", len(result["services"]) + len(result["jobs"]),
              "failures=", result["error_count"])
        return 0 if result["error_count"] == 0 else 2
    except CollectionError as error:
        print("northflank_archive_failed:", str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

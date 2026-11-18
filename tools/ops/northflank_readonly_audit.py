"""Read-only Northflank Core/job config audit; always redact runtime secrets.

Uses only GET endpoints. Never fetches runtime-environment/secrets endpoints,
changes deployment state, runs jobs, or prints raw API responses.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from typing import Any
from urllib import error, request

BASE = "https://api.northflank.com/v1"
SAFE_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,63}\Z")


def _ident(value: str) -> str:
    if not SAFE_ID.fullmatch(value):
        raise ValueError("INVALID_RESOURCE_ID")
    return value


def _object(raw: Any) -> dict:
    if isinstance(raw, dict):
        data = raw.get("data", raw)
        return data if isinstance(data, dict) else {}
    return {}


def _nested(raw: Any, *keys: str) -> Any:
    for name in keys:
        if not isinstance(raw, dict):
            return None
        raw = raw.get(name)
    return raw


def _pick(*values: Any) -> Any:
    return next((v for v in values if v is not None), None)


def _public_kind(value: Any, valid: set[str]) -> str:
    return value if isinstance(value, str) and value in valid else "unknown"


def _expected_path(value: Any, expected: str) -> str:
    if not isinstance(value, str):
        return "unknown"
    return "match" if value.removeprefix("/") == expected.removeprefix("/") else "mismatch"


def _default_cmd(docker: dict, expected: str) -> str:
    kind = _nested(docker, "configType")
    if kind == "default":
        return "default"
    if kind not in ("customCommand", "customEntrypoint", "customEntrypointCustomCommand"):
        return "unknown"
    cmd = _nested(docker, "customCommand")
    if isinstance(cmd, list):
        cmd = " ".join(cmd)
    if isinstance(cmd, str):
        return "matches_expected_custom" if cmd.strip() == expected else "different_custom_command"
    return "custom_unverified"


def _read_config(doc: dict, deploy: dict, *, kind: str) -> dict:
    """Return only allowlisted assertions, never API-provided freeform strings."""
    settings = _nested(doc, "settings") or {}
    deployment = _pick(_nested(deploy, "deployment"), _nested(doc, "deployment"), {})
    docker = _pick(_nested(deployment, "docker"), _nested(doc, "docker"), {})
    build_settings = _pick(_nested(doc, "buildSettings"), _nested(deploy, "buildSettings"), {})
    path = _pick(
        _nested(build_settings, "dockerfile", "dockerFilePath"),
        _nested(doc, "vcsData", "dockerFilePath"),
        _nested(deployment, "vcs", "dockerFilePath"),
    )
    vcs_branch = _pick(
        _nested(doc, "vcsData", "projectBranch"),
        _nested(deployment, "vcs", "projectBranch"),
        _nested(deployment, "internal", "branch"),
    )
    expected_dockerfile = "/deploy/Dockerfile.aiven-preflight" if kind == "aiven" else "/Dockerfile"
    expected_cmd = {
        "core": "amora serve",
        "pcs4": "python -m amora.storage.pcs4_job",
        "aiven": "python /opt/amora/aiven_preflight.py",
    }[kind]
    disabled_cd = doc.get("disabledCD")
    cd = ((not disabled_cd) if type(disabled_cd) is bool else _pick(
        _nested(doc, "cd", "enabled"), _nested(doc, "deployment", "cd", "enabled")))
    disabled_ci = doc.get("disabledCI")
    job_type = _nested(doc, "jobType")
    run_on_change = _nested(settings, "runOnSourceChange")
    cron = _nested(settings, "cron")
    retry = _nested(settings, "backoffLimit")
    volumes = _nested(deployment, "volumes")
    result = {
        "dockerfile": _expected_path(path, expected_dockerfile),
        "command": _default_cmd(docker if isinstance(docker, dict) else {}, expected_cmd),
        "tracked_branch": "main" if vcs_branch == "main" else "other" if isinstance(vcs_branch, str) else "unknown",
        "cd": "enabled" if cd is True else "disabled" if cd is False else "unknown",
        "volume_attachment": "present" if isinstance(volumes, list) and volumes else "none" if isinstance(volumes, list) else "unknown",
    }
    if kind != "core":
        result.update({
            "job_type": _public_kind(job_type, {"manual", "cron"}),
            "ci": "disabled" if disabled_ci is True else "enabled" if disabled_ci is False else "unknown",
            "on_source_change": _public_kind(run_on_change, {"never", "always", "ifSuccess", "onSuccess"}),
            "retry": retry if type(retry) is int and retry in range(0, 21) else "unknown",
            "cron": "not_configured" if cron is None else "configured",
        })
    return result


class NorthflankReader:
    def __init__(self, token: str, team: str, project: str) -> None:
        self.token = token
        self.prefix = f"/teams/{_ident(team)}/projects/{_ident(project)}"

    def get(self, path: str) -> dict:
        if not path.startswith(self.prefix + "/") or "?" in path or ".." in path:
            raise ValueError("INVALID_API_PATH")
        req = request.Request(
            BASE + path,
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/json"},
            method="GET",
        )
        try:
            with request.urlopen(req, timeout=15) as resp:
                body = resp.read(2_000_001)
                if len(body) > 2_000_000:
                    raise ValueError("API_RESPONSE_TOO_LARGE")
                return _object(json.loads(body))
        except error.HTTPError as exc:
            raise ValueError("HTTP_" + str(exc.code)) from None
        except (error.URLError, TimeoutError, OSError):
            raise ValueError("API_REQUEST_FAILED") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("API_RESPONSE_INVALID") from None

    def item(self, category: str, identifier: str) -> tuple[dict, dict]:
        if category not in ("services", "jobs"):
            raise ValueError("INVALID_RESOURCE_TYPE")
        base = self.prefix + "/" + category + "/" + _ident(identifier)
        doc = self.get(base)
        try:
            deployment = self.get(base + "/deployment")
        except ValueError:
            deployment = {}
        return doc, deployment

    def find_aiven_job(self) -> str | None:
        raw = self.get(self.prefix + "/jobs")
        choices = _pick(raw.get("jobs"), raw.get("items"), raw.get("results"), [])
        if isinstance(choices, dict):
            choices = _pick(choices.get("jobs"), choices.get("items"), [])
        if not isinstance(choices, list):
            return None
        hits = [job.get("id") for job in choices if isinstance(job, dict)
                and isinstance(job.get("name"), str)
                and job["name"].strip().casefold() == "aiven"]
        return hits[0] if len(hits) == 1 and isinstance(hits[0], str) else None


def audit(reader: NorthflankReader, core: str, pcs4: str, aiven: str | None) -> dict:
    results = {}
    for kind, category, identifier in (
        ("core", "services", core), ("pcs4", "jobs", pcs4),
        ("aiven", "jobs", aiven or reader.find_aiven_job()),
    ):
        if not identifier:
            results[kind] = {"status": "unavailable", "reason": "JOB_NOT_DISCOVERED"}
            continue
        try:
            doc, deploy = reader.item(category, identifier)
            results[kind] = {"status": "read", **_read_config(doc, deploy, kind=kind)}
        except ValueError as exc:
            results[kind] = {"status": "unavailable", "reason": str(exc)}
    return {
        "status": "requires_operator_review",
        "mode": "read_only_northflank_audit",
        "resources": results,
        "runtime_secret_values_read": False,
        "private_snapshot_verified": False,
        "aiven_data_imported": False,
        "production_cutover_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team", default="mejorberrys-team")
    parser.add_argument("--project", default="default")
    parser.add_argument("--core", default="loyal-coat")
    parser.add_argument("--pcs4-job", default="pcs4c-recovery")
    parser.add_argument("--aiven-job", default=None)
    args = parser.parse_args(argv)
    token = os.getenv("NORTHFLANK_API_TOKEN") or getpass.getpass("Northflank API token (não será exibido): ")
    if not token:
        print(json.dumps({"status": "blocked", "reason": "TOKEN_REQUIRED"}))
        return 2
    try:
        reader = NorthflankReader(token, args.team, args.project)
        report = audit(reader, args.core, args.pcs4_job, args.aiven_job)
    except ValueError as exc:
        report = {"status": "blocked", "reason": str(exc)}
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "requires_operator_review" else 1


if __name__ == "__main__":
    sys.exit(main())

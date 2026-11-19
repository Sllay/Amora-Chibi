"""One-shot Northflank Aiven staging *read-only* operator probe.

Only fixed Northflank resources are accessed. Does not read secrets
endpoints, reveal raw JSON or log contents, or perform Aiven writes.
"""
from __future__ import annotations
import json
import os
import re
import sys
import time
from urllib import request, parse, error

ROOT = "https://api.northflank.com/v1/teams/mejorberrys-team/projects/default/jobs"
PREFIX = "AIVEN_STAGING_OPERATOR "
UUID = re.compile(r"[0-9a-f-]{36}\Z", re.I)
REASON = re.compile(r"(?:PG_STAGING|AIVEN_CA)_[A-Z0-9_]{1,85}\Z")
ALLOWED = {"staging_preparation_possible", "blocked"}
COMMAND = "python -m amora.storage.aiven_staging_operator inspect"


def report(**parts):
    print("AMORA_AIVEN_READONLY_CI " + json.dumps(parts, sort_keys=True), flush=True)


def api(path, method="GET", body=None):
    token = os.environ.get("NF_BACKUP_API_TOKEN", "")
    if not token:
        raise RuntimeError("TOKEN_MISSING")
    if not path.startswith(ROOT) or not path.startswith("https://api.northflank.com/v1/teams/mejorberrys-team/projects/default/jobs"):
        raise RuntimeError("INVALID_PATH")
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        with request.urlopen(request.Request(path, method=method, headers=headers, data=data), timeout=20) as response:
            return json.load(response)
    except error.HTTPError as exc:
        raise RuntimeError("HTTP_" + str(exc.code)) from None
    except Exception:
        raise RuntimeError("REMOTE_API_UNAVAILABLE") from None


def obj(value):
    return value if isinstance(value, dict) else {}


def unwrap(value):
    return obj(obj(value).get("data", value))


def job_id():
    data = unwrap(api(ROOT))
    choices = data.get("jobs", data.get("items", data.get("results", [])))
    if isinstance(choices, dict):
        choices = choices.get("jobs", choices.get("items", []))
    if not isinstance(choices, list):
        raise RuntimeError("JOB_DISCOVERY_UNAVAILABLE")
    hits = [j.get("id") for j in choices
            if isinstance(j, dict) and str(j.get("name", "")).strip().casefold() == "aiven"]
    if len(hits) != 1 or not isinstance(hits[0], str) or not re.fullmatch("[a-zA-Z0-9-]{1,80}", hits[0]):
        raise RuntimeError("JOB_UNEXPECTED")
    return hits[0]


def verify_config(base):
    doc = unwrap(api(base))
    dep = unwrap(api(base + "/deployment"))
    if doc.get("jobType") != "manual":
        raise RuntimeError("JOB_NOT_MANUAL")
    if doc.get("disabledCD") is not True or doc.get("settings", {}).get("runOnSourceChange") != "never":
        raise RuntimeError("AUTOMATIC_JOB_CONFIGURATION")
    if doc.get("settings", {}).get("backoffLimit") != 0:
        raise RuntimeError("JOB_RETRIES_NOT_DISABLED")
    build = obj(doc.get("buildSettings"))
    dockerfile = obj(build.get("dockerfile")).get("dockerFilePath")
    if dockerfile is None:
        dockerfile = obj(doc.get("vcsData")).get("dockerFilePath")
    if not isinstance(dockerfile, str) or dockerfile.lstrip("/") != "deploy/Dockerfile.aiven-staging-operator":
        raise RuntimeError("NOT_STAGING_OPERATOR_IMAGE")
    docker = obj(obj(dep.get("deployment", dep)).get("docker"))
    if docker.get("configType") != "default":
        raise RuntimeError("PERSISTENT_COMMAND_NOT_DEFAULT")
    runs = unwrap(api(base + "/runs?per_page=20")).get("runs", [])
    if not isinstance(runs, list):
        raise RuntimeError("RUN_STATE_UNKNOWN")
    if any(r.get("active") == 1 or r.get("concluded") is False for r in runs if isinstance(r, dict)):
        raise RuntimeError("JOB_ALREADY_RUNNING")


def read_status(base, rid):
    details = unwrap(api(base + "/runs/" + rid))
    status = details.get("status")
    if status not in {"SUCCESS", "RUNNING", "FAILED"}:
        return "UNKNOWN"
    return status


def sanitized_log(base, rid):
    for attempt in range(8):
        params = parse.urlencode({
            "runId": rid, "queryType": "range", "duration": 900,
            "lineLimit": 30, "textIncludes": "AIVEN_STAGING_OPERATOR",
        })
        response = api(base + "/logs?" + params).get("data", [])
        if isinstance(response, list):
            for entry in reversed(response):
                line = entry.get("log", "") if isinstance(entry, dict) else ""
                if PREFIX not in line:
                    continue
                fragment = line.split(PREFIX, 1)[1]
                try:
                    raw = obj(json.JSONDecoder().raw_decode(fragment)[0])
                except Exception:
                    continue
                status = raw.get("status")
                if status not in ALLOWED:
                    continue
                if status == "blocked":
                    code = str(raw.get("reason", "UNKNOWN"))
                    return {"status": status, "reason": code if REASON.fullmatch(code) else "UNKNOWN"}
                return {
                    "status": "staging_preparation_possible",
                    "server_major": raw.get("server_major") if raw.get("server_major") == 18 else "unknown",
                    "database_user_tables": raw.get("database_user_tables") if type(raw.get("database_user_tables")) is int else "unknown",
                    "staging_schema_exists": raw.get("staging_schema_exists") if type(raw.get("staging_schema_exists")) is bool else "unknown",
                    "database_size_bytes": raw.get("database_size_bytes") if type(raw.get("database_size_bytes")) is int else "unknown",
                }
        time.sleep(3)
    return {"status": "report_unavailable"}


def main():
    try:
        jid = job_id()
        base = ROOT + "/" + jid
        verify_config(base)
        run = unwrap(api(base + "/runs", "POST", {
            "deployment": {"docker": {
                "configType": "customCommand",
                "customCommand": COMMAND,
            }},
        }))
        rid = run.get("id")
        if not isinstance(rid, str) or not UUID.fullmatch(rid):
            raise RuntimeError("RUN_ID_UNEXPECTED")
        for step in range(90):
            status = read_status(base, rid)
            if status in {"SUCCESS", "FAILED"}:
                result = sanitized_log(base, rid)
                report(stage="inspect", run_status=status, **result,
                       aiven_writes=False, core_changed=False)
                if status == "SUCCESS" and result.get("status") == "staging_preparation_possible" and result.get("database_user_tables") == 0 and result.get("staging_schema_exists") is False and result.get("server_major") == 18:
                    return 0
                return 2
            time.sleep(4)
        report(stage="inspect", status="timed_out", aiven_writes=False, core_changed=False)
        return 2
    except RuntimeError as exc:
        code = str(exc)
        if not re.fullmatch(r"(?:[A-Z0-9_]{5,80}|HTTP_[1-5][0-9]{2})", code):
            code = "UNKNOWN"
        report(stage="preflight", status="blocked", reason=code,
               aiven_writes=False, core_changed=False)
        return 2


if __name__ == "__main__":
    sys.exit(main())

"""Read-only recovery readiness of existing Northflank services and jobs.

Never fetch secrets, private files, logs or runtime environment.
Only fixed allowlisted labels are emitted, never raw API values.
"""
from __future__ import annotations

import json
import os
from northflank_readonly_audit import NorthflankReader, _nested, _pick

TEAM = "mejorberrys-team"
PROJECT = "default"

def kind(value, allowed):
    return value if type(value) is str and value in allowed else "unknown"

def path_kind(value):
    if not isinstance(value, str):
        return "unknown"
    normalized = "/" + value.lstrip("/")
    known = {
        "/deploy/Dockerfile.aiven-preflight": "aiven_preflight",
        "/deploy/Dockerfile.aiven-staging-operator": "aiven_staging_operator",
        "/deploy/Dockerfile.aiven-private-pcs4c-bridge": "private_bridge",
        "/Dockerfile": "core_runtime",
        "/workers/discord/Dockerfile": "discord_worker",
    }
    return known.get(normalized, "other")

def inspect(reader, category, identifier):
    doc, dep = reader.item(category, identifier)
    docker = _pick(_nested(dep, "deployment", "docker"), _nested(doc, "deployment", "docker"), _nested(dep, "docker"), {})
    source = _pick(_nested(doc, "vcsData", "dockerFilePath"),
                   _nested(doc, "buildSettings", "dockerfile", "dockerFilePath"),
                   _nested(doc, "deployment", "vcs", "dockerFilePath"))
    command_type = _nested(docker, "configType")
    if command_type == "default":
        cmd = "default"
    elif command_type == "customCommand":
        val = _nested(docker, "customCommand")
        if isinstance(val, list):
            val = " ".join(val)
        cmd = ({
            "python -m amora.storage.pcs4_job": "pcs4_recovery",
            "python -m amora.storage.aiven_snapshot_audit": "snapshot_audit",
            "python -m amora.storage.aiven_staging_operator inspect": "staging_readonly",
            "python -m amora.storage.aiven_private_import_operator inspect": "bridge_readonly",
        }).get(str(val).strip(), "other_custom")
    else:
        cmd = "unverified"
    vol = _pick(_nested(dep, "deployment", "volumes"), _nested(doc, "deployment", "volumes"), _nested(dep, "volumes"))
    if isinstance(vol, list):
        attached = "yes" if bool(vol) else "no"
        private_mount = "no"
        for entry in vol:
            if not isinstance(entry, dict):
                continue
            for mount in entry.get("mounts", []):
                if isinstance(mount, dict) and mount.get("containerMountPath") == "/private":
                    private_mount = "yes"
        mounted = private_mount
    else:
        attached = mounted = "unknown"
    status = _nested(doc, "status") or {}
    build = _nested(status, "build", "status")
    deployment = _nested(status, "deployment", "status")
    return {
        "status": "read",
        "dockerfile": path_kind(source),
        "command": cmd,
        "build": kind(build, {"SUCCESS","FAILED","PENDING","BUILDING","IN_PROGRESS","QUEUED"}),
        "deployment": kind(deployment, {"COMPLETED","FAILED","PENDING","DEPLOYING","SUCCESS","RUNNING"}),
        "volume_attached": attached,
        "private_mount": mounted,
    }

def main():
    token = os.environ.get("NORTHFLANK_API_TOKEN")
    if not token:
        print(json.dumps({"status": "blocked", "reason": "TOKEN_REQUIRED"}))
        return 2
    reader = NorthflankReader(token, TEAM, PROJECT)
    results = {}
    aiven_id = None
    try:
        aiven_id = reader.find_aiven_job()
    except ValueError:
        pass
    for name, category, ident in [
        ("core", "services", "loyal-coat"),
        ("pcs4", "jobs", "pcs4c-recovery"),
        ("aiven", "jobs", aiven_id),
    ]:
        if ident is None:
            results[name] = {"status": "unavailable", "reason": "JOB_NOT_DISCOVERED"}
            continue
        try:
            results[name] = inspect(reader, category, ident)
        except ValueError as exc:
            code = str(exc)
            if not (code.startswith("HTTP_") or code in ("API_REQUEST_FAILED","API_RESPONSE_INVALID")):
                code = "READ_FAILED"
            results[name] = {"status": "unavailable", "reason": code}
    print(json.dumps({
        "status": "read_only",
        "resources": results,
        "secrets_inspected": False,
        "files_accessed": False,
        "changes_made": False,
        "cutover_authorized": False,
    }, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

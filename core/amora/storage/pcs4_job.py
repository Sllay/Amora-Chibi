"""One-shot PCS.4C job, invoked by the existing Dev panel; never serves HTTP."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

from .pcs4_web import RecoveryPanel, _mounted
from .pcs4_contract import CONFIRMATIONS, MARKER, STEPS as _STEPS
from .remote import LibSQLBackend



def run(panel, action):
    if action == "review_restore":
        return panel.review_restore()
    if action not in _STEPS:
        raise ValueError("INVALID_ACTION")
    panel.start(action, CONFIRMATIONS[action])
    while True:
        state = panel.status()
        if state["status"] != "running":
            return state
        time.sleep(0.2)


def main():
    try:
        action = os.getenv("AMORA_PCS4_JOB_ACTION", "")
        if action not in CONFIRMATIONS or os.getenv("AMORA_PCS4_JOB_ENABLED") != "1":
            raise ValueError("JOB_DISABLED")
        mount = Path(os.getenv("AMORA_PCS4_WEB_VOLUME", "/private")).resolve()
        if os.getenv("AMORA_PCS4_WEB_DURABLE") != "1" or not _mounted(mount):
            raise ValueError("DURABLE_VOLUME_REQUIRED")
        source = LibSQLBackend(os.environ["AMORA_LIBSQL_URL"],
                               os.environ["AMORA_SECRET_LIBSQL_AUTH_TOKEN"])
        panel = RecoveryPanel(mount / "pcs4c", source, online_snapshot=True)
        result = run(panel, action)
        result["action"] = action
        print(MARKER + json.dumps(result, separators=(",", ":")), flush=True)
        return 0 if result["status"] == "ok" and (action in result["completed"] or action == "review_restore") else 1
    except Exception as exc:
        reason = "JOB_FAILED_REVIEW_REQUIRED"
        if action == "review_restore" and isinstance(exc, ValueError) and exc.args and exc.args[0] in {
            "PCS4C_REVIEW_STATE", "PCS4C_REVIEW_DESTINATION_CHANGED",
            "PCS4C_DESTINATION_IS_SOURCE", "PCS4C_SCHEMA_MISMATCH",
            "PCS4C_DATA_MISMATCH", "PCS4C_SOURCE_CHANGED",
        }:
            reason = exc.args[0]
        print(MARKER + json.dumps({"status": "error", "reason": reason},
                                  separators=(",", ":")), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

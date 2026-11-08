"""Verify effective project agent roles without inference or spawned agents.

Print only agent/layer metadata, never the full configuration or credentials.
Requires the installed Codex app-server config/read protocol.
"""
from __future__ import annotations

import json
from pathlib import Path
import selectors
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = {"amora_test", "core_especionar"}


def main() -> None:
    process = subprocess.Popen(
        ["codex", "--strict-config", "app-server", "--stdio"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, cwd=ROOT,
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        messages = [
            {"id": 1, "method": "initialize", "params": {
                "clientInfo": {"name": "amora-phase1-audit", "version": "1.0"},
                "capabilities": {"experimentalApi": True}}},
            {"method": "initialized"},
            {"id": 2, "method": "config/read", "params": {
                "cwd": str(ROOT), "includeLayers": True}},
        ]
        for message in messages:
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if not selector.select(timeout=1):
                    continue
                line = process.stdout.readline()
                if not line:
                    raise RuntimeError("Codex exited before config/read")
                reply = json.loads(line)
                if reply.get("id") != 2:
                    continue
                if "error" in reply:
                    raise RuntimeError("Codex config/read failed")
                result = reply["result"]
                agents = result.get("config", {}).get("agents") or {}
                roles = {name for name, value in agents.items()
                         if isinstance(value, dict) and "config_file" in value}
                assert roles == EXPECTED, f"Unexpected effective roles: {sorted(roles)}"
                assert agents.get("max_concurrent_threads_per_session") == 2
                for name in roles:
                    path = Path(agents[name]["config_file"])
                    assert path.is_file() and path.is_relative_to(ROOT / ".codex")
                project_layers = [item for item in result.get("layers", [])
                                  if item.get("name", {}).get("type") == "project"]
                assert any(item.get("disabledReason") is None and
                           item.get("config", {}).get("agents", {}).get("amora_test")
                           for item in project_layers), "Project layer is not active"
                print(json.dumps({"verified": True, "roles": sorted(roles),
                                  "max_concurrent_threads_per_session": 2,
                                  "project_layer_active": True}))
                return
            raise TimeoutError("Codex config/read deadline exceeded")
    finally:
        process.terminate()
        process.wait(timeout=5)


if __name__ == "__main__":
    main()

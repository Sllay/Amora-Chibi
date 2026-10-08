from __future__ import annotations

import argparse
import json

from amora import __version__
from amora.api import create_api
from amora.app import AmoraCore


def doctor() -> int:
    core = AmoraCore.bootstrap()
    try:
        report = core.health_report() | {"runtime_home": str(core.paths.home), "database": str(core.paths.database), "api_bind": "127.0.0.1"}
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["status"] == "ok" else 1
    finally:
        core.close()


def backup(name: str | None) -> int:
    core = AmoraCore.bootstrap()
    try:
        path = core.create_backup(name)
        print(path)
        return 0
    finally:
        core.close()


def serve(port: int) -> int:
    import uvicorn
    core = AmoraCore.bootstrap()
    app = create_api(core)
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_config=None)
    finally:
        core.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="amora")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Valida fundações locais do Core")
    backup_parser = sub.add_parser("backup", help="Cria backup consistente do SQLite")
    backup_parser.add_argument("--name", default=None)
    serve_parser = sub.add_parser("serve", help="Inicia API local do Core em 127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.command == "doctor": return doctor()
    if args.command == "backup": return backup(args.name)
    if args.command == "serve": return serve(args.port)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

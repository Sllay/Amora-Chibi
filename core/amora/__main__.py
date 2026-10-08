from __future__ import annotations

import argparse
import json

from amora.app import AmoraCore


def doctor() -> int:
    core = AmoraCore.bootstrap()
    try:
        bk = core.identities.resolve_alias("Slay")
        report = {
            "status": "ok",
            "runtime_home": str(core.paths.home),
            "database": str(core.paths.database),
            "database_integrity": core.database.integrity_check(),
            "identity_alias_test": bk.id if bk else None,
            "model_providers_registered": 0,
            "version": "0.1.0.dev0",
        }
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["database_integrity"] == "ok" else 1
    finally:
        core.close()


def main() -> int:
    parser = argparse.ArgumentParser(prog="amora")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Valida fundações locais do Core")
    args = parser.parse_args()
    if args.command == "doctor":
        return doctor()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

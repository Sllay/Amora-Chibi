from __future__ import annotations

import argparse
import json

from amora.app import AmoraCore


def _dump(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def state() -> int:
    core = AmoraCore.bootstrap()
    try:
        _dump(core.avatar.semantic_state().model_dump(mode="json"))
        return 0
    finally:
        core.close()


def project() -> int:
    core = AmoraCore.bootstrap()
    try:
        _dump(core.avatar.project().model_dump(mode="json"))
        return 0
    finally:
        core.close()


def assets() -> int:
    core = AmoraCore.bootstrap()
    try:
        _dump([item.model_dump(mode="json") for item in core.avatar.store.list_assets()])
        return 0
    finally:
        core.close()


def main() -> int:
    parser = argparse.ArgumentParser(prog="amora-avatar", description="Diagnóstico local do Avatar V0.9")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("state")
    sub.add_parser("project")
    sub.add_parser("assets")
    sub.add_parser("url")
    args = parser.parse_args()

    if args.command == "state":
        return state()
    if args.command == "project":
        return project()
    if args.command == "assets":
        return assets()
    if args.command == "url":
        _dump({
            "studio": "http://127.0.0.1:8765/avatar/",
            "runtime": "http://127.0.0.1:8765/avatar/?mode=runtime",
        })
        return 0
    return 2

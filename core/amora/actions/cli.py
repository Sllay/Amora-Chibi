from __future__ import annotations

import argparse
import asyncio
import json

from amora.app import AmoraCore


def _dump(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def state() -> int:
    core = AmoraCore.bootstrap()
    try:
        _dump(core.actions.state())
        return 0
    finally:
        core.close()


def plan(args: argparse.Namespace) -> int:
    core = AmoraCore.bootstrap()
    try:
        result = asyncio.run(core.actions.plan_current(dedupe_key=args.dedupe_key))
        if result is None:
            _dump({"planned": False, "reason": "NO_ACTIVE_OR_SUPPORTED_INTENTION"})
            return 1
        _dump(result.model_dump(mode="json"))
        return 0
    finally:
        core.close()


def execute(args: argparse.Namespace) -> int:
    core = AmoraCore.bootstrap()
    try:
        if args.plan_id:
            result = asyncio.run(core.actions.execute_plan(args.plan_id, dedupe_key=args.dedupe_key))
        else:
            result = asyncio.run(core.actions.execute_current(dedupe_key=args.dedupe_key))
        if result is None:
            _dump({"executed": False, "reason": "NO_ACTIVE_OR_SUPPORTED_INTENTION"})
            return 1
        _dump(result.model_dump(mode="json"))
        return 0
    finally:
        core.close()


def plans(args: argparse.Namespace) -> int:
    core = AmoraCore.bootstrap()
    try:
        _dump([item.model_dump(mode="json") for item in core.actions.store.list_plans(limit=args.limit)])
        return 0
    finally:
        core.close()


def executions(args: argparse.Namespace) -> int:
    core = AmoraCore.bootstrap()
    try:
        _dump([item.model_dump(mode="json") for item in core.actions.store.list_executions(limit=args.limit)])
        return 0
    finally:
        core.close()


def main() -> int:
    parser = argparse.ArgumentParser(prog="amora-actions", description="Debug local de Actions/Planner V0.7")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("state", help="Mostra plugins, tools, skills e contadores")

    plan_parser = sub.add_parser("plan", help="Planeja a intenção BDI ativa sem executar")
    plan_parser.add_argument("--dedupe-key", default=None)

    execute_parser = sub.add_parser("execute", help="Executa e verifica o plano/intenção atual")
    execute_parser.add_argument("--plan-id", default=None)
    execute_parser.add_argument("--dedupe-key", default=None)

    plans_parser = sub.add_parser("plans", help="Lista planos persistidos")
    plans_parser.add_argument("--limit", type=int, default=20)

    exec_parser = sub.add_parser("executions", help="Lista ToolCalls persistidos")
    exec_parser.add_argument("--limit", type=int, default=50)

    args = parser.parse_args()
    if args.command == "state":
        return state()
    if args.command == "plan":
        return plan(args)
    if args.command == "execute":
        return execute(args)
    if args.command == "plans":
        return plans(args)
    if args.command == "executions":
        return executions(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

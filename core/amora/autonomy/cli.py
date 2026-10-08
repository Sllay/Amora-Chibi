from __future__ import annotations

import argparse
import asyncio
import json

from amora.app import AmoraCore

from .models import DecisionContext, DriveType


def _dump(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def state() -> int:
    core = AmoraCore.bootstrap()
    try:
        _dump(core.autonomy.state())
        return 0
    finally:
        core.close()


def cycle(args: argparse.Namespace) -> int:
    core = AmoraCore.bootstrap()
    try:
        context = DecisionContext(
            is_idle=not args.not_idle,
            can_interrupt=not args.no_interrupt,
            active_conversation=args.active_conversation,
            active_addressee=args.active_addressee,
            available_entities=set(args.available),
            busy_entities=set(args.busy),
            opportunity=args.opportunity,
            interruption_penalty=args.interruption_penalty,
        )
        result = asyncio.run(core.autonomy.cycle(context, dedupe_key=args.dedupe_key))
        _dump(result.model_dump(mode="json"))
        return 0
    finally:
        core.close()


def adjust(args: argparse.Namespace) -> int:
    core = AmoraCore.bootstrap()
    try:
        result, deduplicated = core.autonomy.adjust_drive(
            DriveType(args.drive),
            args.delta,
            reason=args.reason,
            dedupe_key=args.dedupe_key,
        )
        _dump({"drive": result.model_dump(mode="json"), "deduplicated": deduplicated})
        return 0
    finally:
        core.close()


def decisions(args: argparse.Namespace) -> int:
    core = AmoraCore.bootstrap()
    try:
        _dump([item.model_dump(mode="json") for item in core.autonomy.store.list_decisions(limit=args.limit)])
        return 0
    finally:
        core.close()


def main() -> int:
    parser = argparse.ArgumentParser(prog="amora-autonomy", description="Debug local da Autonomy V0.6")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("state", help="Mostra drives, intenção ativa e contador de decisões")

    cycle_parser = sub.add_parser("cycle", help="Executa um ciclo BDI sem executar a ação")
    cycle_parser.add_argument("--not-idle", action="store_true")
    cycle_parser.add_argument("--no-interrupt", action="store_true")
    cycle_parser.add_argument("--active-conversation", action="store_true")
    cycle_parser.add_argument("--active-addressee", default=None)
    cycle_parser.add_argument("--available", action="append", default=[])
    cycle_parser.add_argument("--busy", action="append", default=[])
    cycle_parser.add_argument("--opportunity", type=float, default=1.0)
    cycle_parser.add_argument("--interruption-penalty", type=float, default=0.0)
    cycle_parser.add_argument("--dedupe-key", default=None)

    drive_parser = sub.add_parser("drive", help="Ajusta um drive de forma auditável")
    drive_parser.add_argument("drive", choices=[item.value for item in DriveType])
    drive_parser.add_argument("delta", type=float)
    drive_parser.add_argument("--reason", required=True)
    drive_parser.add_argument("--dedupe-key", default=None)

    decisions_parser = sub.add_parser("decisions", help="Lista decisões persistidas")
    decisions_parser.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()
    if args.command == "state":
        return state()
    if args.command == "cycle":
        return cycle(args)
    if args.command == "drive":
        return adjust(args)
    if args.command == "decisions":
        return decisions(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

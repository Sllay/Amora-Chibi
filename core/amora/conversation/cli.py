from __future__ import annotations

import argparse
import asyncio
import json

from amora.app import AmoraCore
from amora.events import PrivacyScope

from .models import SpeechAck, TurnSignal, UtteranceKind


def _dump(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def state() -> int:
    core = AmoraCore.bootstrap()
    try:
        _dump(core.conversation.state())
        return 0
    finally:
        core.close()


def turn(args: argparse.Namespace) -> int:
    core = AmoraCore.bootstrap()
    try:
        signal = TurnSignal(
            speaker_id=args.speaker,
            addressee_id=args.addressee,
            conversation_floor=args.floor,
            vad_active=args.vad_active,
            pause_ms=args.pause_ms,
            partial_text=args.text,
            direct_call=args.direct_call,
            speech_requested=args.speech_requested,
            active_conversation=args.active_conversation,
            can_interrupt=not args.no_interrupt,
            urgency=args.urgency,
            privacy_scope=PrivacyScope(args.privacy),
            dedupe_key=args.dedupe_key,
        )
        if args.backchannel:
            decision, utterance = asyncio.run(core.conversation.enqueue_backchannel(signal, dedupe_key=args.dedupe_key))
            _dump({
                "decision": decision.model_dump(mode="json"),
                "utterance": utterance.model_dump(mode="json") if utterance else None,
            })
        else:
            _dump(core.conversation.decide_turn(signal).model_dump(mode="json"))
        return 0
    finally:
        core.close()


def say(args: argparse.Namespace) -> int:
    core = AmoraCore.bootstrap()
    try:
        item = asyncio.run(core.conversation.enqueue_text(
            args.text,
            target_id=args.target,
            kind=UtteranceKind.SEMANTIC,
            priority=args.priority,
            privacy_scope=PrivacyScope.PERSONAL,
            source_event_id=None,
            dedupe_key=args.dedupe_key,
            metadata={"debug_cli": True},
        ))
        _dump(item.model_dump(mode="json") if item else {"queued": False})
        return 0 if item else 1
    finally:
        core.close()


def next_item(args: argparse.Namespace) -> int:
    core = AmoraCore.bootstrap()
    try:
        item = core.conversation.claim_next(args.device)
        _dump({"utterance": item.model_dump(mode="json") if item else None})
        return 0
    finally:
        core.close()


def ack(args: argparse.Namespace) -> int:
    core = AmoraCore.bootstrap()
    try:
        item = asyncio.run(core.conversation.acknowledge(args.utterance_id, SpeechAck(spoken=not args.retry)))
        _dump({"utterance": item.model_dump(mode="json") if item else None})
        return 0 if item else 1
    finally:
        core.close()


def utterances(args: argparse.Namespace) -> int:
    core = AmoraCore.bootstrap()
    try:
        _dump([item.model_dump(mode="json") for item in core.conversation.store.list_utterances(limit=args.limit)])
        return 0
    finally:
        core.close()


def turns(args: argparse.Namespace) -> int:
    core = AmoraCore.bootstrap()
    try:
        _dump([item.model_dump(mode="json") for item in core.conversation.store.list_turns(limit=args.limit)])
        return 0
    finally:
        core.close()


def main() -> int:
    parser = argparse.ArgumentParser(prog="amora-conversation", description="Debug local de conversa/voz V0.8")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("state")

    turn_parser = sub.add_parser("turn")
    turn_parser.add_argument("--speaker", default="PERSON_BK")
    turn_parser.add_argument("--addressee", default=None)
    turn_parser.add_argument("--floor", default=None)
    turn_parser.add_argument("--vad-active", action="store_true")
    turn_parser.add_argument("--pause-ms", type=int, default=0)
    turn_parser.add_argument("--text", default=None)
    turn_parser.add_argument("--direct-call", action="store_true")
    turn_parser.add_argument("--speech-requested", action="store_true")
    turn_parser.add_argument("--active-conversation", action="store_true")
    turn_parser.add_argument("--no-interrupt", action="store_true")
    turn_parser.add_argument("--urgency", type=float, default=0.0)
    turn_parser.add_argument("--privacy", choices=[item.value for item in PrivacyScope], default=PrivacyScope.PERSONAL.value)
    turn_parser.add_argument("--backchannel", action="store_true")
    turn_parser.add_argument("--dedupe-key", default=None)

    say_parser = sub.add_parser("say")
    say_parser.add_argument("text")
    say_parser.add_argument("--target", default="PERSON_BK")
    say_parser.add_argument("--priority", type=int, default=60)
    say_parser.add_argument("--dedupe-key", default=None)

    next_parser = sub.add_parser("next")
    next_parser.add_argument("--device", default="DEVICE_A15")

    ack_parser = sub.add_parser("ack")
    ack_parser.add_argument("utterance_id")
    ack_parser.add_argument("--retry", action="store_true")

    utter_parser = sub.add_parser("utterances")
    utter_parser.add_argument("--limit", type=int, default=20)
    turns_parser = sub.add_parser("turns")
    turns_parser.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()
    if args.command == "state": return state()
    if args.command == "turn": return turn(args)
    if args.command == "say": return say(args)
    if args.command == "next": return next_item(args)
    if args.command == "ack": return ack(args)
    if args.command == "utterances": return utterances(args)
    if args.command == "turns": return turns(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

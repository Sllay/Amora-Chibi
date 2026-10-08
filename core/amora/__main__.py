from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import mimetypes
from pathlib import Path

from amora import __version__
from amora.api import create_api
from amora.app import AmoraCore
from amora.audio import AudioSegment, SttError
from amora.language import PerspectiveFrame, PerspectiveStack
from amora.memory import MemoryCandidate, MemoryKind, MemoryProvenance, MemoryQuery, ProvenanceKind


def doctor() -> int:
    core = AmoraCore.bootstrap()
    try:
        report = core.health_report() | {
            "runtime_home": str(core.paths.home),
            "database": str(core.paths.database),
            "api_bind": "127.0.0.1",
        }
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


def issue_pairing_code() -> int:
    core = AmoraCore.bootstrap()
    try:
        issued = core.pairing.issue()
        print(f"Código de pareamento: {issued.code}")
        print(f"Expira em: {issued.expires_at.isoformat()}")
        print("Uso único; máximo de 5 tentativas.")
        return 0
    finally:
        core.close()


def resolve_reference(
    expression: str,
    *,
    speaker: str,
    addressee: str | None,
    focus: str | None,
    candidates: list[str],
) -> int:
    core = AmoraCore.bootstrap()
    try:
        stack = PerspectiveStack(
            PerspectiveFrame(
                speaker_id=speaker,
                addressee_id=addressee,
                focus_entity_id=focus,
            )
        )
        result = core.perspective.resolve(expression, stack, candidates=candidates)
        print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
        return 0
    finally:
        core.close()


def infer_addressee(text: str, *, speaker: str) -> int:
    core = AmoraCore.bootstrap()
    try:
        result = core.addressee.infer(text, speaker_id=speaker, scene=core.social.scene)
        print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
        return 0
    finally:
        core.close()


def secret_set(name: str) -> int:
    value = getpass.getpass(f"Valor para {name.upper()}: ")
    if not value:
        print("Erro: valor vazio.")
        return 2
    core = AmoraCore.bootstrap()
    try:
        normalized = core.secrets.normalize_name(name)
        core.secrets.set(normalized, value)
        print(f"{normalized} configurado no SecretStore local.")
        return 0
    finally:
        core.close()


def secret_list() -> int:
    core = AmoraCore.bootstrap()
    try:
        for name in core.secrets.names():
            print(name)
        return 0
    finally:
        core.close()


def secret_delete(name: str) -> int:
    core = AmoraCore.bootstrap()
    try:
        normalized = core.secrets.normalize_name(name)
        deleted = core.secrets.delete(normalized)
        print(f"{normalized}: {'removido' if deleted else 'não encontrado'}")
        return 0
    finally:
        core.close()


def transcribe(path: str, *, language: str | None) -> int:
    audio_path = Path(path).expanduser().resolve()
    mime_type = mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
    core = AmoraCore.bootstrap()
    try:
        try:
            result = asyncio.run(
                core.stt.transcribe(
                    AudioSegment(
                        path=audio_path,
                        mime_type=mime_type,
                        language=language,
                    )
                )
            )
        except (SttError, FileNotFoundError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
            return 2
        print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
        return 0
    finally:
        core.close()


def memory_remember(
    text: str,
    *,
    kind: str,
    actor: str,
    entities: list[str],
    tags: list[str],
    importance: float,
    novelty: float,
) -> int:
    core = AmoraCore.bootstrap()
    try:
        entity_refs = {entity_id: 1.0 for entity_id in entities}
        if actor and actor not in entity_refs:
            entity_refs[actor] = 1.0
        result = core.memory.remember(
            MemoryCandidate(
                kind=MemoryKind(kind),
                content=text,
                provenance=MemoryProvenance(
                    kind=ProvenanceKind.USER_ASSERTION,
                    source="cli:memory",
                    actor_id=actor,
                    confidence=1.0,
                ),
                entity_refs=entity_refs,
                tags=set(tags),
                importance=importance,
                novelty=novelty,
                relationship_relevance=0.7 if entity_refs else 0.0,
                future_consequence=0.35,
                rarity=0.35,
                goal_relevance=0.35,
            )
        )
        print(json.dumps({
            "decision": result.decision.model_dump(mode="json"),
            "memory": result.memory.model_dump(mode="json") if result.memory else None,
        }, indent=2, ensure_ascii=False))
        return 0 if result.memory is not None else 1
    finally:
        core.close()


def memory_recall(text: str, *, entities: list[str], limit: int) -> int:
    core = AmoraCore.bootstrap()
    try:
        hits = core.memory.recall(
            MemoryQuery(text=text, entity_ids=set(entities), limit=limit)
        )
        print(json.dumps([hit.model_dump(mode="json") for hit in hits], indent=2, ensure_ascii=False))
        return 0
    finally:
        core.close()


def main() -> int:
    parser = argparse.ArgumentParser(prog="amora")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Valida fundações locais do Core")
    sub.add_parser("pair", help="Gera código local de pareamento de uso único")

    backup_parser = sub.add_parser("backup", help="Cria backup consistente do SQLite")
    backup_parser.add_argument("--name", default=None)

    serve_parser = sub.add_parser("serve", help="Inicia API local do Core em 127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)

    resolve_parser = sub.add_parser("resolve", help="Debug do Perspective Resolver V0.2")
    resolve_parser.add_argument("expression")
    resolve_parser.add_argument("--speaker", default="PERSON_BK")
    resolve_parser.add_argument("--addressee", default="ENTITY_AMORA")
    resolve_parser.add_argument("--focus", default=None)
    resolve_parser.add_argument("--candidate", action="append", default=[])

    addressee_parser = sub.add_parser("addressee", help="Debug do Addressee Engine V0.2")
    addressee_parser.add_argument("text")
    addressee_parser.add_argument("--speaker", default="PERSON_BK")

    secret_parser = sub.add_parser("secret", help="Gerencia apenas nomes/valores no SecretStore local")
    secret_sub = secret_parser.add_subparsers(dest="secret_command", required=True)
    secret_set_parser = secret_sub.add_parser("set", help="Lê um segredo sem ecoar no terminal")
    secret_set_parser.add_argument("name")
    secret_sub.add_parser("list", help="Lista somente nomes de secrets, nunca valores")
    secret_delete_parser = secret_sub.add_parser("delete", help="Remove um secret local")
    secret_delete_parser.add_argument("name")

    transcribe_parser = sub.add_parser("transcribe", help="Transcreve arquivo de áudio via STT configurado")
    transcribe_parser.add_argument("path")
    transcribe_parser.add_argument("--language", default="pt")

    memory_parser = sub.add_parser("memory", help="Debug local da memória V0.3")
    memory_sub = memory_parser.add_subparsers(dest="memory_command", required=True)
    memory_remember_parser = memory_sub.add_parser("remember", help="Submete uma lembrança ao Memory Write Gate")
    memory_remember_parser.add_argument("text")
    memory_remember_parser.add_argument("--kind", choices=[kind.value for kind in MemoryKind], default=MemoryKind.EPISODIC.value)
    memory_remember_parser.add_argument("--actor", default="PERSON_BK")
    memory_remember_parser.add_argument("--entity", action="append", default=[])
    memory_remember_parser.add_argument("--tag", action="append", default=[])
    memory_remember_parser.add_argument("--importance", type=float, default=0.8)
    memory_remember_parser.add_argument("--novelty", type=float, default=0.8)
    memory_recall_parser = memory_sub.add_parser("recall", help="Busca memória com retrieval híbrido local")
    memory_recall_parser.add_argument("text")
    memory_recall_parser.add_argument("--entity", action="append", default=[])
    memory_recall_parser.add_argument("--limit", type=int, default=8)

    args = parser.parse_args()
    if args.command == "doctor":
        return doctor()
    if args.command == "pair":
        return issue_pairing_code()
    if args.command == "backup":
        return backup(args.name)
    if args.command == "serve":
        return serve(args.port)
    if args.command == "resolve":
        return resolve_reference(
            args.expression,
            speaker=args.speaker,
            addressee=args.addressee,
            focus=args.focus,
            candidates=args.candidate,
        )
    if args.command == "addressee":
        return infer_addressee(args.text, speaker=args.speaker)
    if args.command == "secret":
        if args.secret_command == "set":
            return secret_set(args.name)
        if args.secret_command == "list":
            return secret_list()
        if args.secret_command == "delete":
            return secret_delete(args.name)
    if args.command == "transcribe":
        return transcribe(args.path, language=args.language)
    if args.command == "memory":
        if args.memory_command == "remember":
            return memory_remember(
                args.text,
                kind=args.kind,
                actor=args.actor,
                entities=args.entity,
                tags=args.tag,
                importance=args.importance,
                novelty=args.novelty,
            )
        if args.memory_command == "recall":
            return memory_recall(args.text, entities=args.entity, limit=args.limit)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

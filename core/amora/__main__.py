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


def main() -> int:
    parser = argparse.ArgumentParser(prog="amora")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Valida fundações locais do Core")

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

    args = parser.parse_args()
    if args.command == "doctor":
        return doctor()
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
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

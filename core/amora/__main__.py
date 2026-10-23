from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import mimetypes
from amora.settings import ServerSettings
from pathlib import Path

from amora import __version__
from amora.api import create_api
from amora.app import AmoraCore
from amora.audio import AudioSegment, SttError
from amora.emotion import AppraisalInput
from amora.language import PerspectiveFrame, PerspectiveStack
from amora.memory import MemoryCandidate, MemoryKind, MemoryProvenance, MemoryQuery, ProvenanceKind
from amora.world import EpistemicKind, WorldClaimDraft


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


def serve(port: int | None = None, host: str | None = None) -> int:
    import uvicorn

    core = AmoraCore.bootstrap()
    settings = ServerSettings.from_environment()
    app = create_api(core, own_core=True)
    try:
        uvicorn.run(app, host=host or settings.host, port=port or settings.port, log_config=None, timeout_graceful_shutdown=30)
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
        hits = core.memory.recall(MemoryQuery(text=text, entity_ids=set(entities), limit=limit))
        print(json.dumps([hit.model_dump(mode="json") for hit in hits], indent=2, ensure_ascii=False))
        return 0
    finally:
        core.close()


def _jsonish(value: str):
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def world_record(
    statement: str,
    *,
    kind: str,
    subject: str | None,
    predicate: str,
    value: str,
    confidence: float,
    source: str,
) -> int:
    core = AmoraCore.bootstrap()
    try:
        result = core.world.record(
            WorldClaimDraft(
                kind=EpistemicKind(kind),
                statement=statement,
                subject_id=subject,
                predicate=predicate,
                object_value=_jsonish(value),
                source=source,
                source_entity_id="PERSON_BK" if source == "cli:world" else None,
                confidence=confidence,
            )
        )
        print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
        return 0
    finally:
        core.close()


def world_state(*, subject: str | None) -> int:
    core = AmoraCore.bootstrap()
    try:
        state = core.world.current_state(subject_id=subject)
        print(json.dumps([claim.model_dump(mode="json") for claim in state], indent=2, ensure_ascii=False))
        return 0
    finally:
        core.close()


def world_history(*, subject: str | None, predicate: str | None, limit: int) -> int:
    core = AmoraCore.bootstrap()
    try:
        history = core.world.history(subject_id=subject, predicate=predicate, limit=limit)
        print(json.dumps([claim.model_dump(mode="json") for claim in history], indent=2, ensure_ascii=False))
        return 0
    finally:
        core.close()


def self_show() -> int:
    core = AmoraCore.bootstrap()
    try:
        print(json.dumps(core.self_model.snapshot(), indent=2, ensure_ascii=False))
        return 0
    finally:
        core.close()


def emotion_appraise(
    description: str,
    *,
    valence: float,
    target: str | None,
    agent: str | None,
    goal_relevance: float,
    novelty: float,
    expectedness: float,
    agency: float,
    control: float,
    intentionality: float,
    norm_violation: float,
    relationship_relevance: float,
    dedupe_key: str | None,
) -> int:
    core = AmoraCore.bootstrap()
    try:
        try:
            result = asyncio.run(
                core.emotion.process(
                    AppraisalInput(
                        description=description,
                        outcome_valence=valence,
                        target_id=target,
                        agent_id=agent,
                        goal_relevance=goal_relevance,
                        novelty=novelty,
                        expectedness=expectedness,
                        agency=agency,
                        control=control,
                        intentionality=intentionality,
                        norm_violation=norm_violation,
                        relationship_relevance=relationship_relevance,
                        dedupe_key=dedupe_key,
                    )
                )
            )
        except ValueError as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
            return 2
        print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
        return 0
    finally:
        core.close()


def emotion_state() -> int:
    core = AmoraCore.bootstrap()
    try:
        payload = {
            "mood": core.emotion.current_mood().model_dump(mode="json"),
            "affect": [
                core.emotion.affect_for(item.entity_id).model_dump(mode="json")
                for item in core.emotion.store.list_affect()
            ],
            "pending_bids": [item.model_dump(mode="json") for item in core.emotion.pending_bids()],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    finally:
        core.close()


def emotion_affect(entity_id: str) -> int:
    core = AmoraCore.bootstrap()
    try:
        try:
            affect = core.emotion.affect_for(entity_id)
        except ValueError as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
            return 2
        print(json.dumps(affect.model_dump(mode="json"), indent=2, ensure_ascii=False))
        return 0
    finally:
        core.close()


def emotion_events(*, target: str | None, limit: int) -> int:
    core = AmoraCore.bootstrap()
    try:
        items = core.emotion.store.list_events(target_id=target, limit=limit)
        print(json.dumps([item.model_dump(mode="json") for item in items], indent=2, ensure_ascii=False))
        return 0
    finally:
        core.close()


def emotion_bids(*, limit: int) -> int:
    core = AmoraCore.bootstrap()
    try:
        print(json.dumps([item.model_dump(mode="json") for item in core.emotion.pending_bids(limit=limit)], indent=2, ensure_ascii=False))
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
    serve_parser.add_argument("--port", type=int, default=None)
    serve_parser.add_argument("--host", default=None)

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

    world_parser = sub.add_parser("world", help="Debug local do World Model V0.4")
    world_sub = world_parser.add_subparsers(dest="world_command", required=True)
    world_record_parser = world_sub.add_parser("record", help="Registra observation/report/belief/inference/hypothesis")
    world_record_parser.add_argument("statement")
    world_record_parser.add_argument("--kind", choices=[kind.value for kind in EpistemicKind], default=EpistemicKind.REPORT.value)
    world_record_parser.add_argument("--subject", default=None)
    world_record_parser.add_argument("--predicate", required=True)
    world_record_parser.add_argument("--value", required=True, help="JSON simples ou texto")
    world_record_parser.add_argument("--confidence", type=float, default=1.0)
    world_record_parser.add_argument("--source", default="cli:world")
    world_state_parser = world_sub.add_parser("state", help="Mostra beliefs canônicos ativos")
    world_state_parser.add_argument("--subject", default=None)
    world_history_parser = world_sub.add_parser("history", help="Mostra histórico epistêmico, inclusive revisões expiradas")
    world_history_parser.add_argument("--subject", default=None)
    world_history_parser.add_argument("--predicate", default=None)
    world_history_parser.add_argument("--limit", type=int, default=100)

    self_parser = sub.add_parser("self", help="Debug local do Self Model V0.4")
    self_sub = self_parser.add_subparsers(dest="self_command", required=True)
    self_sub.add_parser("show", help="Mostra snapshot do Self Model")

    emotion_parser = sub.add_parser("emotion", help="Debug local do Emotion Engine V0.5")
    emotion_sub = emotion_parser.add_subparsers(dest="emotion_command", required=True)
    emotion_appraise_parser = emotion_sub.add_parser("appraise", help="Avalia um acontecimento pelo Appraisal Engine")
    emotion_appraise_parser.add_argument("description")
    emotion_appraise_parser.add_argument("--valence", type=float, required=True, help="impacto -1..1")
    emotion_appraise_parser.add_argument("--target", default=None)
    emotion_appraise_parser.add_argument("--agent", default=None)
    emotion_appraise_parser.add_argument("--goal-relevance", type=float, default=0.5)
    emotion_appraise_parser.add_argument("--novelty", type=float, default=0.5)
    emotion_appraise_parser.add_argument("--expectedness", type=float, default=0.5)
    emotion_appraise_parser.add_argument("--agency", type=float, default=0.5)
    emotion_appraise_parser.add_argument("--control", type=float, default=0.5)
    emotion_appraise_parser.add_argument("--intentionality", type=float, default=0.5)
    emotion_appraise_parser.add_argument("--norm-violation", type=float, default=0.0)
    emotion_appraise_parser.add_argument("--relationship-relevance", type=float, default=0.0)
    emotion_appraise_parser.add_argument("--dedupe-key", default=None)
    emotion_sub.add_parser("state", help="Mostra mood, entity affect e social bids pendentes")
    emotion_affect_parser = emotion_sub.add_parser("affect", help="Mostra afeto direcionado a uma entidade")
    emotion_affect_parser.add_argument("entity_id")
    emotion_events_parser = emotion_sub.add_parser("events", help="Lista Emotion Events persistidos")
    emotion_events_parser.add_argument("--target", default=None)
    emotion_events_parser.add_argument("--limit", type=int, default=50)
    emotion_bids_parser = emotion_sub.add_parser("bids", help="Lista Social Bids candidatos ainda pendentes")
    emotion_bids_parser.add_argument("--limit", type=int, default=50)

    args = parser.parse_args()
    if args.command == "doctor":
        return doctor()
    if args.command == "pair":
        return issue_pairing_code()
    if args.command == "backup":
        return backup(args.name)
    if args.command == "serve":
        return serve(args.port, args.host)
    if args.command == "resolve":
        return resolve_reference(args.expression, speaker=args.speaker, addressee=args.addressee, focus=args.focus, candidates=args.candidate)
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
            return memory_remember(args.text, kind=args.kind, actor=args.actor, entities=args.entity, tags=args.tag, importance=args.importance, novelty=args.novelty)
        if args.memory_command == "recall":
            return memory_recall(args.text, entities=args.entity, limit=args.limit)
    if args.command == "world":
        if args.world_command == "record":
            return world_record(args.statement, kind=args.kind, subject=args.subject, predicate=args.predicate, value=args.value, confidence=args.confidence, source=args.source)
        if args.world_command == "state":
            return world_state(subject=args.subject)
        if args.world_command == "history":
            return world_history(subject=args.subject, predicate=args.predicate, limit=args.limit)
    if args.command == "self" and args.self_command == "show":
        return self_show()
    if args.command == "emotion":
        if args.emotion_command == "appraise":
            return emotion_appraise(
                args.description,
                valence=args.valence,
                target=args.target,
                agent=args.agent,
                goal_relevance=args.goal_relevance,
                novelty=args.novelty,
                expectedness=args.expectedness,
                agency=args.agency,
                control=args.control,
                intentionality=args.intentionality,
                norm_violation=args.norm_violation,
                relationship_relevance=args.relationship_relevance,
                dedupe_key=args.dedupe_key,
            )
        if args.emotion_command == "state":
            return emotion_state()
        if args.emotion_command == "affect":
            return emotion_affect(args.entity_id)
        if args.emotion_command == "events":
            return emotion_events(target=args.target, limit=args.limit)
        if args.emotion_command == "bids":
            return emotion_bids(limit=args.limit)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

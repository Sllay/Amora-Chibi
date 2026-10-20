from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import CognitiveContext


class RealizationClosing(StrEnum):
    NONE = "none"
    STATEMENT = "statement"
    QUESTION = "question"


class RealizationMove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1200)


class RealizationFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    moves: list[RealizationMove] = Field(min_length=1, max_length=5)
    closing: RealizationClosing = RealizationClosing.NONE


@dataclass(frozen=True, slots=True)
class ValidatedRealization:
    frame: RealizationFrame
    text: str
    reason_codes: tuple[str, ...]


class RealizationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RealizationValidator:
    """Validate provider wording against the Core-owned DialoguePlan."""

    _LOCAL_ACTS = {"acknowledge_call", "answer_self_fact"}

    @classmethod
    def parse_and_validate(cls, context: CognitiveContext, raw: object) -> ValidatedRealization:
        if not isinstance(raw, dict):
            raise RealizationError("REALIZATION_ROOT_NOT_OBJECT", "realization root must be an object")
        try:
            frame = RealizationFrame.model_validate(raw)
        except ValidationError as exc:
            raise RealizationError("REALIZATION_SCHEMA_INVALID", str(exc)) from exc
        return cls.validate(context, frame)

    @classmethod
    def validate(cls, context: CognitiveContext, frame: RealizationFrame) -> ValidatedRealization:
        plan = context.dialogue_plan
        allowed_moves = {str(value) for value in plan.get("response_moves") or []}
        if not allowed_moves:
            raise RealizationError("DIALOGUE_PLAN_HAS_NO_MOVES", "dialogue plan has no authorized response moves")

        emitted_kinds = [move.kind for move in frame.moves]
        unauthorized = [kind for kind in emitted_kinds if kind not in allowed_moves]
        if unauthorized:
            raise RealizationError(
                "UNAUTHORIZED_REALIZATION_MOVE",
                f"provider emitted unauthorized moves: {unauthorized}",
            )

        ask_followup = bool(plan.get("ask_followup", False))
        offer_help = bool(plan.get("offer_help", False))
        closing_mode = str(plan.get("closing_mode") or "natural")
        combined = " ".join(move.text.strip() for move in frame.moves if move.text.strip()).strip()
        if not combined:
            raise RealizationError("EMPTY_REALIZATION", "realization has no speakable text")

        if not ask_followup:
            if frame.closing == RealizationClosing.QUESTION:
                raise RealizationError("FOLLOWUP_NOT_AUTHORIZED", "question closing is not authorized")
            if combined.rstrip().endswith("?"):
                raise RealizationError("FOLLOWUP_NOT_AUTHORIZED", "realization ends in an unauthorized question")
            if any(kind in {"ask_followup", "ask_question", "request_more"} for kind in emitted_kinds):
                raise RealizationError("FOLLOWUP_NOT_AUTHORIZED", "follow-up move is not authorized")

        if not offer_help and any(kind in {"offer_help", "offer_support", "invite_request"} for kind in emitted_kinds):
            raise RealizationError("HELP_OFFER_NOT_AUTHORIZED", "help-offer move is not authorized")

        if closing_mode in {"none", "statement"} and frame.closing == RealizationClosing.QUESTION:
            raise RealizationError("CLOSING_MODE_VIOLATION", f"closing mode {closing_mode} forbids a question")
        if closing_mode == "question" and ask_followup and frame.closing != RealizationClosing.QUESTION:
            raise RealizationError("CLOSING_MODE_VIOLATION", "dialogue plan requires a question closing")

        depth = str(plan.get("depth") or "medium")
        max_chars = {"short": 360, "medium": 900, "deep": 1800}.get(depth, 900)
        if len(combined) > max_chars:
            raise RealizationError(
                "REALIZATION_TOO_LONG",
                f"realization length {len(combined)} exceeds {max_chars} for depth={depth}",
            )

        return ValidatedRealization(
            frame=frame,
            text=combined,
            reason_codes=(
                "REALIZATION_SCHEMA_VALID",
                "REALIZATION_MOVES_AUTHORIZED",
                "DIALOGUE_PLAN_ENFORCED_LOCALLY",
            ),
        )

    @classmethod
    def local_realization(cls, context: CognitiveContext) -> ValidatedRealization | None:
        plan = context.dialogue_plan
        act = str(plan.get("act") or "")
        if act not in cls._LOCAL_ACTS:
            return None

        if act == "acknowledge_call":
            text = "Oi, papis." if _speaker_is_parent(context) else "Oi."
            frame = RealizationFrame(
                moves=[RealizationMove(kind="acknowledge_call", text=text)],
                closing=RealizationClosing.STATEMENT,
            )
            validated = cls.validate(context, frame)
            return ValidatedRealization(
                frame=validated.frame,
                text=validated.text,
                reason_codes=validated.reason_codes + ("LOCAL_REALIZATION_NO_PROVIDER",),
            )

        fact_key = str(plan.get("fact_key") or "")
        status = str(plan.get("fact_status") or "")
        value = plan.get("fact_value")
        if status != "known" or value in {None, ""}:
            move_kind = "acknowledge_self_fact_undefined"
            text = "Isso ainda não tá definido pra mim."
        else:
            move_kind = "answer_requested_self_fact"
            if fact_key == "canonical_name":
                text = f"Meu nome é {value}."
            elif fact_key == "age":
                text = f"Tenho {value} anos."
            elif fact_key == "birth_date":
                text = f"Minha data de nascimento registrada é {value}."
            elif fact_key == "activation_date":
                text = f"Minha data de ativação registrada é {value}."
            else:
                text = str(value)

        frame = RealizationFrame(
            moves=[RealizationMove(kind=move_kind, text=text)],
            closing=RealizationClosing.STATEMENT,
        )
        validated = cls.validate(context, frame)
        return ValidatedRealization(
            frame=validated.frame,
            text=validated.text,
            reason_codes=validated.reason_codes + ("LOCAL_SELF_FACT_REALIZATION",),
        )


def realization_instruction(context: CognitiveContext) -> str:
    plan = context.dialogue_plan
    allowed = [str(value) for value in plan.get("response_moves") or []]
    specialist = ""
    if context.specialist_support:
        specialist = (
            "\nNOTA DE ESPECIALISTA INTERNO — APOIO EFÊMERO, NÃO CANÔNICO:\n"
            + json.dumps(context.specialist_support, ensure_ascii=False, separators=(",", ":"))
            + "\nUse essa nota somente como apoio factual/racional. Não mencione o especialista, provider, modelo ou a nota. "
            "Se a nota declarar incerteza, preserve essa incerteza na fala."
        )

    return (
        "Retorne SOMENTE um objeto JSON válido, sem markdown e sem texto fora do objeto. "
        "Formato exato: {\"moves\":[{\"kind\":\"...\",\"text\":\"...\"}],\"closing\":\"none|statement|question\"}. "
        f"Os únicos valores permitidos para moves[].kind neste turno são: {allowed}. "
        "Você está realizando A FALA DA AMORA, em primeira pessoa, como uma personagem presente naquela conversa; "
        "não fale sobre a Amora de fora e não use voz de assistente, atendente, relatório ou tutorial genérico. "
        "O Core continua sendo a autoridade sobre quem ela é: não invente memória, relação, sentimento, fato pessoal ou objetivo social. "
        "Cada text será reproduzido em voz alta, não exibido como resposta de chat. Escreva português brasileiro oral e natural. "
        "Não use markdown, bullets, numeração visual, tabelas, títulos, colchetes, marcações de ação ou símbolos para carregar significado. "
        "Quando precisar dar exemplo, verbalize naturalmente 'por exemplo'. Quando houver opções ou sequência, diga em fala corrida "
        "'uma opção...', 'outra...', 'primeiro...', 'depois...' conforme fizer sentido. "
        "Prefira frases curtas, contrações e ritmo de conversa. Para depth=short, em geral uma ou duas frases bastam; "
        "para depth=medium, evite palestra; depth=deep permite explicar mais sem virar artigo. "
        "Não termine oferecendo ajuda nem perguntando 'mais alguma coisa?' se o plano não autorizou isso. "
        f"ask_followup={bool(plan.get('ask_followup', False))}; "
        f"offer_help={bool(plan.get('offer_help', False))}; "
        f"closing_mode={plan.get('closing_mode', 'natural')}. "
        "Não crie nenhum move, objetivo social, fato ou pergunta fora do plano."
        + specialist
    )


def _speaker_is_parent(context: CognitiveContext) -> bool:
    if context.speaker_id != "PERSON_BK":
        return False
    for relation in context.relationships:
        relation_type = str(relation.get("relation_type") or "").casefold()
        if relation_type in {"father", "parent", "pai"} or "parent" in relation_type:
            return True
    return False

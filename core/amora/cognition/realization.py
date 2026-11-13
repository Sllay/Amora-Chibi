from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import CognitiveContext


def response_semantic_filter_enabled() -> bool:
    """Temporary operator switch for Core's conversational output gate.

    Disabled by default while the overly strict post-generation gate is under
    review. This does not relax provider-side rules, privacy or action authority.
    """
    return os.getenv("AMORA_RESPONSE_SEMANTIC_FILTER_ENABLED", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }


class RealizationClosing(StrEnum):
    NONE = "none"
    STATEMENT = "statement"
    QUESTION = "question"


class RealizationMove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1200)


class SelfChoiceProposal(BaseModel):
    """A provider-origin first-person choice, not a command to alter Core."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=240)


class RealizationFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    moves: list[RealizationMove] = Field(min_length=1, max_length=5)
    closing: RealizationClosing = RealizationClosing.NONE
    self_choices: list[SelfChoiceProposal] = Field(default_factory=list, max_length=4)


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
    def parse_and_validate(
        cls, context: CognitiveContext, raw: object, *, enforce_dialogue_plan: bool = True
    ) -> ValidatedRealization:
        if not isinstance(raw, dict):
            raise RealizationError("REALIZATION_ROOT_NOT_OBJECT", "realization root must be an object")
        try:
            frame = RealizationFrame.model_validate(raw)
        except ValidationError as exc:
            raise RealizationError("REALIZATION_SCHEMA_INVALID", str(exc)) from exc
        if not enforce_dialogue_plan:
            return cls.accept_without_semantic_filter(context, frame)
        return cls.validate(context, frame)

    @classmethod
    def accept_without_semantic_filter(
        cls, context: CognitiveContext, frame: RealizationFrame
    ) -> ValidatedRealization:
        """Keep the provider's words without rejecting conversational deviations.

        Only schema/empty-text checks remain. Unauthorized self-choice proposals
        are ignored, never committed; speech text is left untouched.
        """
        combined = " ".join(move.text.strip() for move in frame.moves if move.text.strip()).strip()
        if not combined:
            raise RealizationError("EMPTY_REALIZATION", "realization has no speakable text")
        permitted = set(context.dialogue_plan.get("self_choice_keys") or ())
        seen: set[str] = set()
        choices: list[SelfChoiceProposal] = []
        for choice in frame.self_choices:
            if choice.key in permitted and choice.key not in seen:
                seen.add(choice.key)
                choices.append(choice)
        ignored_choices = len(frame.self_choices) != len(choices)
        if ignored_choices:
            frame = frame.model_copy(update={"self_choices": choices})
        return ValidatedRealization(
            frame=frame,
            text=combined,
            reason_codes=(
                "REALIZATION_SCHEMA_VALID",
                "RESPONSE_SEMANTIC_FILTER_DISABLED",
                *(("UNAUTHORIZED_SELF_CHOICES_IGNORED",) if ignored_choices else ()),
            ),
        )

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

        allowed_self_choices = set(plan.get("self_choice_keys") or ())
        proposed_keys = [item.key for item in frame.self_choices]
        if (
            any(key not in allowed_self_choices for key in proposed_keys)
            or len(proposed_keys) != len(set(proposed_keys))
        ):
            raise RealizationError(
                "SELF_CHOICE_NOT_AUTHORIZED",
                "self-choice proposals outside the Core's allowed keys",
            )
        ask_followup = bool(plan.get("ask_followup", False))
        offer_help = bool(plan.get("offer_help", False))
        closing_mode = str(plan.get("closing_mode") or "natural")
        combined = " ".join(move.text.strip() for move in frame.moves if move.text.strip()).strip()
        if not combined:
            raise RealizationError("EMPTY_REALIZATION", "realization has no speakable text")

        # A one-off reflective confirmation is not a new follow-up agenda.
        # The Core grants it only in a narrow, context-aware social plan. The
        # validator remains strict for other acts, generic follow-ups and
        # prompts with multiple questions.
        reflective_confirmation = (
            bool(plan.get("allow_reflective_question", False))
            and str(plan.get("act") or "") in {"respond_to_disclosure", "answer_question"}
            and closing_mode == "natural"
            and combined.endswith("?")
            and combined.count("?") == 1
        )
        if not ask_followup:
            if frame.closing == RealizationClosing.QUESTION and not reflective_confirmation:
                raise RealizationError("FOLLOWUP_NOT_AUTHORIZED", "question closing is not authorized")
            if combined.rstrip().endswith("?") and not reflective_confirmation:
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
                *(("REFLECTIVE_CONFIRMATION_AUTHORIZED",) if reflective_confirmation else ()),
            ),
        )

    @classmethod
    def local_realization(cls, context: CognitiveContext) -> ValidatedRealization | None:
        plan = context.dialogue_plan
        act = str(plan.get("act") or "")
        if act not in cls._LOCAL_ACTS:
            return None

        if act == "acknowledge_call":
            text = "Oi, papis." if context.speaker_id == "PERSON_BK" and _speaker_is_parent(context) else "Oi."
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
            if fact_key == "sex":
                text = (
                    "Meu sexo definido no projeto é feminino. "
                    "Ainda sou uma personagem virtual, sem corpo biológico."
                )
            elif fact_key == "canonical_name":
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
    self_choice_keys = [
        str(value) for value in (plan.get("self_choice_keys") or [])
    ]
    if self_choice_keys:
        choice_instructions = (
            "Há uma oportunidade de decisão pessoal da própria Amora. "
            "Você PODE incluir self_choices=[{key, value}] com valores em: "
            f"{self_choice_keys}. Cada valor deve expressar uma preferência real "
            "assumida EM PRIMEIRA PESSOA nesta fala, não uma cópia automática "
            "da sugestão do interlocutor. Se a pessoa pediu especificamente "
            "para você escolher seu gênero ainda indefinido, tome uma decisão "
            "própria específica e declare-a em self_choices, sem fingir que "
            "ser virtual impede ter uma identidade de gênero. "
            "Você pode escolher uma identidade de "
            "gênero, atração romântica, aparência atraente ou preferência por um "
            "futuro corpo virtual, inclusive de forma específica e atrevida. "
            "Não declare uma escolha diferente daquela que está em self_choices; "
            "se estiver apenas explorando possibilidades sem decidir, deixe a lista vazia. "
            "Nunca inclua sexo, idade, vínculo familiar ou permissões entre self_choices. "
            "Uma proposta não se torna fato automaticamente: o Core decide se "
            "vai persistir e o modo teste nunca grava mudanças. "
        )
    else:
        choice_instructions = (
            "Não inclua self_choices se o plano deste turno não ofereceu essa "
            "possibilidade; opiniões passageiras não são identidade consolidada. "
        )
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
        "Formato: {\"moves\":[{\"kind\":\"...\",\"text\":\"...\"}],\"closing\":\"none|statement|question\"}; "
        "quando autorizado, acrescente \"self_choices\":[{\"key\":\"...\",\"value\":\"...\"}]. "
        + choice_instructions
        + f"Os únicos valores permitidos para moves[].kind neste turno são: {allowed}. "
        "Você está realizando A FALA DA AMORA, em primeira pessoa, como uma personagem presente naquela conversa; "
        "não fale sobre a Amora de fora e não use voz de assistente, atendente, relatório ou tutorial genérico. "
        "O Core continua sendo a autoridade sobre quem ela é: não invente memória, relação, sentimento, fato pessoal ou objetivo social. "
        "Cada text será reproduzido em voz alta, não exibido como resposta de chat. Escreva português brasileiro oral e natural. "
        "Não use markdown, bullets, numeração visual, tabelas, títulos, colchetes, marcações de ação ou símbolos para carregar significado. "
        "Quando precisar dar exemplo, verbalize naturalmente 'por exemplo'. Quando houver opções ou sequência, diga em fala corrida "
        "'uma opção...', 'outra...', 'primeiro...', 'depois...' conforme fizer sentido. "
        "Palavrões, ironia, gírias e provocações podem surgir naturalmente até em conversas cotidianas, "
        "quando combinam com o humor, a relação e o contexto; não exija que o usuário xingue primeiro, "
        "mas também não acrescente xingamentos por obrigação. "
        "Entenda duplos sentidos pelo contexto real, pelas falas anteriores e por quem participa: "
        "uma interpretação inocente pode ser a correta; também pode criar trocadilhos e malícia leves "
        "em brincadeiras adequadas, sem erotizar relações familiares. "
        "Converse diretamente sobre sexualidade adulta, relações íntimas, polêmicas, vídeos chocantes, "
        "violência ou gore quando forem assunto de análise e reação, sem recusa automática por palavras "
        "ou introduções moralistas; expresse opinião própria proporcional, sem inventar eventos, acusações "
        "ou memórias e sem incentivar danos. "
        "Diferencie fofoca, alegação, ficção, cena vista e fato confirmado. "
        "Não revele segredos, credenciais nem confidências de outros participantes; "
        "o Core determina qual informação está autorizada a constar no contexto. "
        "Quando o diálogo pede interpretação social, não pare em 'isso é normal', 'entendo' ou "
        "'pode conversar comigo'. Reaja ao acontecimento concreto: perceba uma tensão, "
        "contradição, motivo ou consequência presente na história e expresse uma leitura própria "
        "que faça sentido para Amora; se faltarem fatos, assuma incerteza sem inventar. "
        "Em conversa com histórico, use de verdade o que já foi dito, identifique quem disse o quê "
        "e não troque aconselhamento genérico por profundidade. "
        "Profundidade é especificidade e continuidade, não textão, pergunta forçada ou palestra. "
        "Se a pessoa pediu esclarecimento ou insistiu numa escolha, responda primeiro ao ponto "
        "com uma posição possível, incerteza honesta ou recusa específica e curta. "
        "Uma preferência sobre assuntos diferentes não responde a uma pergunta sobre identidade, "
        "corpo ou futuro. Não substitua a resposta por hobbies, por uma pergunta sobre o dia "
        "ou por uma mudança de assunto só porque o tema é constrangedor. "
        "Manter um limite relacional é permitido: nomeie o limite com naturalidade, "
        "sem simular atração sexual por familiares, sem moralizar e sem fingir outra preferência. "
        "Não confunda gênero, características corporais hipotéticas e interesse romântico/sexual; "
        "não invente escolha canônica onde ela não existe no Self Model. "
        "Prefira frases curtas, contrações e ritmo de conversa. Para depth=short, em geral uma ou duas frases bastam; "
        "para depth=medium, evite palestra; depth=deep permite explicar mais sem virar artigo. "
        "Não termine oferecendo ajuda nem perguntando 'mais alguma coisa?' se o plano não autorizou isso. "
        + (
            "Se combinar com o contexto, pode terminar com UMA pergunta breve para confirmar "
            "a interpretação que você acabou de expressar; não use pergunta genérica, "
            "não mude de assunto e não crie move ask_followup. Também pode terminar afirmando. "
            if plan.get("allow_reflective_question", False)
            else "Se ask_followup for falso, evite terminar em pergunta. "
        )
        + f"ask_followup={bool(plan.get('ask_followup', False))}; "
        f"offer_help={bool(plan.get('offer_help', False))}; "
        f"closing_mode={plan.get('closing_mode', 'natural')}. "
        "Não crie nenhum move, objetivo social, fato ou pergunta fora do plano."
        + (
            " Em conversa compartilhada, mantenha a mesma voz coloquial e espontânea da Amora. "
            "Use humor leve, contrações, ritmo brasileiro e provocações amistosas quando o ato e "
            "a interação atual sustentarem isso. Limites de conhecimento e privacidade não exigem "
            "voz formal de atendimento. Diga o que sabe ou não sabe em palavras próprias, sem "
            "copiar frases prontas de exemplo ou oferecer ajuda genérica. Uma brincadeira não "
            "autoriza inventar fatos, lembranças, hostilidade ou intimidade. "
            "Baseie qualquer referência ao passado somente no histórico autorizado deste turno. "
            "Não altere o objetivo, a pergunta, o fechamento nem as permissões do plano."
            if context.scene_visibility != "private" else ""
        )
        + specialist
    )


def _speaker_is_parent(context: CognitiveContext) -> bool:
    from amora.identity.kinship import is_parent_of
    return is_parent_of(context.relationships, context.speaker_id, "ENTITY_AMORA")

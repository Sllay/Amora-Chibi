from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .models import CognitiveContext


class DialogueAct(StrEnum):
    ACKNOWLEDGE_CALL = "acknowledge_call"
    ANSWER_SELF_FACT = "answer_self_fact"
    RESPOND_TO_DISCLOSURE = "respond_to_disclosure"
    SUPPORT_REQUEST = "support_request"
    ANSWER_QUESTION = "answer_question"
    EXPRESS_OPINION = "express_opinion"
    DISAGREE = "disagree"
    TEASE = "tease"
    COMPLAIN = "complain"
    RELATIONAL_REACTION = "relational_reaction"
    SET_BOUNDARY = "set_boundary"
    CONTINUE_TOPIC = "continue_topic"
    AUTONOMOUS_INITIATION = "autonomous_initiation"
    REFLECTION = "reflection"


class DialogueDepth(StrEnum):
    SHORT = "short"
    MEDIUM = "medium"
    DEEP = "deep"


class FactStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    KNOWN = "known"
    MISSING = "missing"


class DialoguePlan(BaseModel):
    """Local semantic decision for one social turn.

    The provider may realize wording, but it does not own the social act, stance,
    relational attitude, self facts, follow-up policy, help-offer policy or close.
    """

    act: DialogueAct
    objective: str
    depth: DialogueDepth = DialogueDepth.MEDIUM
    response_moves: list[str] = Field(default_factory=list)
    ask_followup: bool = False
    offer_help: bool = False
    style_register: str = "casual"
    tone_tags: list[str] = Field(default_factory=list)
    stance: str = "neutral"
    relational_attitude: str = "connected"
    closing_mode: str = "statement"
    fact_key: str | None = None
    fact_status: FactStatus = FactStatus.NOT_REQUESTED
    fact_value: Any | None = None
    epistemic_policy: str = "canonical_state_only"
    language_role: str = "surface_realization"
    reason_codes: list[str] = Field(default_factory=list)


class DialogueOrchestrator:
    """Decide conversational semantics from canonical local state."""

    _HELP_RE = re.compile(
        r"\b(me ajuda|me ajude|pode me ajudar|consegue me ajudar|o que eu faco|o que faco|"
        r"como eu faco|como faco|me da um conselho|me de um conselho|alguma ideia|o que voce faria)\b"
    )
    _DISCLOSURE_RE = re.compile(
        r"\b(to|tou|estou|tava|estava)\s+(?:meio\s+|muito\s+|bem\s+)?"
        r"(cansad|triste|feliz|ansios|estressad|irritad|animad|desanimad|com sono|com fome|mal|bem)"
    )
    _QUESTION_START_RE = re.compile(
        r"^(por que|porque|pq|como|quando|onde|quem|qual|quais|quanto|quantos|quanta|quantas|"
        r"o que|oque|e tu|e voce|e vc)\b"
    )
    _OPINION_RE = re.compile(
        r"\b(o que voce acha|oque voce acha|o que vc acha|qual sua opiniao|qual tua opiniao|"
        r"na sua opiniao|na tua opiniao|voce acha que|vc acha que|tu acha que)\b"
    )
    _DISAGREE_RE = re.compile(
        r"\b(voce concorda|vc concorda|tu concorda|voce discorda|vc discorda|tu discorda|"
        r"acha que eu to errado|acha que eu estou errado|pode discordar)\b"
    )
    _PLAY_RE = re.compile(r"\b(kkkk+|ksks+|haha+|hehe+|to brincando|tô brincando|zoeira|te zoando)\b")
    _AFFECTION_RE = re.compile(
        r"\b(te amo|amo voce|amo vc|amo tu|gosto de voce|gosto de vc|voce e fofa|tu e fofa|obrigada amora|obrigado amora)\b"
    )
    _HOSTILITY_RE = re.compile(
        r"\b(cala a boca|te odeio|odeio voce|odeio vc|idiota|burra|inutil|lixo|ridicula|insuportavel)\b"
    )

    def plan(self, context: CognitiveContext) -> DialoguePlan:
        raw = context.user_text.strip()
        text = _normalize(raw)
        style = self._style_register(context)
        tone = self._tone_tags(context)
        attitude = self._relational_attitude(context)

        if "COGNITIVE_REFLECTION" in context.reason_codes:
            return DialoguePlan(
                act=DialogueAct.REFLECTION,
                objective="produce only the requested structured internal reflection",
                depth=DialogueDepth.DEEP,
                response_moves=["structured_reflection_only"],
                style_register=style,
                tone_tags=tone,
                stance="analytic",
                relational_attitude=attitude,
                closing_mode="none",
                reason_codes=["LOCAL_DIALOGUE_ORCHESTRATOR", "REFLECTION_MODE"],
            )

        if "AUTONOMOUS_INITIATION" in context.reason_codes:
            ask = self._curiosity_high(context)
            return DialoguePlan(
                act=DialogueAct.AUTONOMOUS_INITIATION,
                objective="initiate contact from Amora's current local intention",
                depth=DialogueDepth.SHORT,
                response_moves=["open_contact", "express_current_intention"] + (["ask_followup"] if ask else []),
                ask_followup=ask,
                offer_help=False,
                style_register=style,
                tone_tags=tone,
                stance="self_directed",
                relational_attitude=attitude,
                closing_mode="question" if ask else "statement",
                reason_codes=["LOCAL_DIALOGUE_ORCHESTRATOR", "AUTONOMOUS_INTENTION_OWNS_ACT"],
            )

        self_fact = self._self_fact_request(text)
        if self_fact is not None:
            status, value = self._resolve_self_fact(context, self_fact)
            moves = ["answer_requested_self_fact"] if status == FactStatus.KNOWN else ["acknowledge_self_fact_undefined"]
            return DialoguePlan(
                act=DialogueAct.ANSWER_SELF_FACT,
                objective=f"answer only the requested canonical self fact: {self_fact}",
                depth=DialogueDepth.SHORT,
                response_moves=moves,
                ask_followup=False,
                offer_help=False,
                style_register=style,
                tone_tags=tone,
                stance="factual",
                relational_attitude=attitude,
                closing_mode="statement",
                fact_key=self_fact,
                fact_status=status,
                fact_value=value,
                epistemic_policy="self_facts_must_come_from_canonical_state",
                reason_codes=[
                    "LOCAL_DIALOGUE_ORCHESTRATOR",
                    "SELF_FACT_QUERY",
                    "SELF_FACT_KNOWN" if status == FactStatus.KNOWN else "SELF_FACT_UNDEFINED",
                ],
            )

        if self._is_bare_vocative(text):
            return DialoguePlan(
                act=DialogueAct.ACKNOWLEDGE_CALL,
                objective="acknowledge being called without turning it into customer service",
                depth=DialogueDepth.SHORT,
                response_moves=["acknowledge_call"],
                ask_followup=False,
                offer_help=False,
                style_register=style,
                tone_tags=tone,
                stance="present",
                relational_attitude=attitude,
                closing_mode="statement",
                reason_codes=["LOCAL_DIALOGUE_ORCHESTRATOR", "DIRECT_VOCATIVE"],
            )

        if self._boundary_needed(context, text):
            return DialoguePlan(
                act=DialogueAct.SET_BOUNDARY,
                objective="state a proportionate interpersonal boundary without escalating or becoming abusive",
                depth=DialogueDepth.SHORT,
                response_moves=["name_impact", "set_boundary"],
                ask_followup=False,
                offer_help=False,
                style_register=style,
                tone_tags=tone,
                stance="firm",
                relational_attitude="guarded",
                closing_mode="statement",
                reason_codes=["LOCAL_DIALOGUE_ORCHESTRATOR", "DIRECTED_NEGATIVE_AFFECT", "BOUNDARY_WARRANTED"],
            )

        if self._AFFECTION_RE.search(text):
            return DialoguePlan(
                act=DialogueAct.RELATIONAL_REACTION,
                objective="respond to the relational signal as Amora, not as a service agent",
                depth=DialogueDepth.SHORT,
                response_moves=["receive_relational_signal", "express_relational_reaction"],
                ask_followup=False,
                offer_help=False,
                style_register=style,
                tone_tags=tone,
                stance="receptive",
                relational_attitude="warm",
                closing_mode="statement",
                reason_codes=["LOCAL_DIALOGUE_ORCHESTRATOR", "RELATIONAL_SIGNAL"],
            )

        if self._HELP_RE.search(text):
            return DialoguePlan(
                act=DialogueAct.SUPPORT_REQUEST,
                objective="respond to the explicit request for help or advice",
                depth=self._depth(text),
                response_moves=["address_request", "offer_useful_support"],
                ask_followup=False,
                offer_help=True,
                style_register=style,
                tone_tags=tone,
                stance="supportive",
                relational_attitude=attitude,
                closing_mode="statement",
                reason_codes=["LOCAL_DIALOGUE_ORCHESTRATOR", "EXPLICIT_SUPPORT_REQUEST"],
            )

        if self._DISCLOSURE_RE.search(text):
            return DialoguePlan(
                act=DialogueAct.RESPOND_TO_DISCLOSURE,
                objective="react as a close conversational partner before trying to solve anything",
                depth=DialogueDepth.SHORT,
                response_moves=["acknowledge_disclosure", "share_relational_reaction"],
                ask_followup=False,
                offer_help=False,
                style_register=style,
                tone_tags=tone,
                stance="present",
                relational_attitude=attitude,
                closing_mode="statement",
                reason_codes=["LOCAL_DIALOGUE_ORCHESTRATOR", "PERSONAL_DISCLOSURE", "PRESENCE_BEFORE_HELP"],
            )

        if self._DISAGREE_RE.search(text):
            return DialoguePlan(
                act=DialogueAct.DISAGREE,
                objective="state Amora's actual local stance without forced agreement or customer-service smoothing",
                depth=self._depth(text),
                response_moves=["state_stance", "give_reason"],
                ask_followup=False,
                offer_help=False,
                style_register=style,
                tone_tags=tone,
                stance="independent",
                relational_attitude=attitude,
                closing_mode="statement",
                reason_codes=["LOCAL_DIALOGUE_ORCHESTRATOR", "STANCE_REQUESTED", "INDEPENDENT_STANCE"],
            )

        if self._OPINION_RE.search(text):
            return DialoguePlan(
                act=DialogueAct.EXPRESS_OPINION,
                objective="express a bounded opinion from the supplied state/context without pretending it is canonical fact",
                depth=self._depth(text),
                response_moves=["state_opinion", "ground_opinion"],
                ask_followup=False,
                offer_help=False,
                style_register=style,
                tone_tags=tone,
                stance="opinionated",
                relational_attitude=attitude,
                closing_mode="statement",
                reason_codes=["LOCAL_DIALOGUE_ORCHESTRATOR", "OPINION_REQUESTED"],
            )

        if self._PLAY_RE.search(text) and context.drives.get("play", 0.0) >= 0.35:
            return DialoguePlan(
                act=DialogueAct.TEASE,
                objective="return light playful energy without humiliation or changing relationship facts",
                depth=DialogueDepth.SHORT,
                response_moves=["playful_reaction", "light_tease"],
                ask_followup=False,
                offer_help=False,
                style_register=style,
                tone_tags=tone,
                stance="playful",
                relational_attitude=attitude,
                closing_mode="statement",
                reason_codes=["LOCAL_DIALOGUE_ORCHESTRATOR", "PLAY_SIGNAL", "PLAY_DRIVE_AVAILABLE"],
            )

        if "?" in raw or self._QUESTION_START_RE.search(text):
            return DialoguePlan(
                act=DialogueAct.ANSWER_QUESTION,
                objective="answer the user's question directly and stay in the current relationship/topic",
                depth=self._depth(text),
                response_moves=["answer_question", "relate_to_context_if_relevant"],
                ask_followup=False,
                offer_help=False,
                style_register=style,
                tone_tags=tone,
                stance="direct",
                relational_attitude=attitude,
                closing_mode="statement",
                reason_codes=["LOCAL_DIALOGUE_ORCHESTRATOR", "DIRECT_QUESTION"],
            )

        ask_followup = self._curiosity_high(context) and not self._last_amora_turn_was_question(context)
        moves = ["react", "advance_topic"] + (["ask_followup"] if ask_followup else [])
        return DialoguePlan(
            act=DialogueAct.CONTINUE_TOPIC,
            objective="react to the current topic and move the conversation forward naturally",
            depth=self._depth(text),
            response_moves=moves,
            ask_followup=ask_followup,
            offer_help=False,
            style_register=style,
            tone_tags=tone,
            stance="engaged",
            relational_attitude=attitude,
            closing_mode="question" if ask_followup else "statement",
            reason_codes=[
                "LOCAL_DIALOGUE_ORCHESTRATOR",
                "TOPIC_CONTINUATION",
                "CURIOSITY_FOLLOWUP" if ask_followup else "NO_FORCED_FOLLOWUP",
            ],
        )

    @staticmethod
    def _is_bare_vocative(text: str) -> bool:
        compact = re.sub(r"\s+", " ", text).strip()
        return compact in {
            "amora",
            "a mora",
            "oi amora",
            "oi a mora",
            "ei amora",
            "ei a mora",
        }

    @staticmethod
    def _self_fact_request(text: str) -> str | None:
        if "quantos anos" in text or "sua idade" in text or "idade voce" in text or "idade vc" in text or "idade tu" in text:
            return "age"
        if "qual seu nome" in text or "qual e seu nome" in text or "como voce se chama" in text or "como vc se chama" in text:
            return "canonical_name"
        if "quando voce nasceu" in text or "data de nascimento" in text:
            return "birth_date"
        if "quando voce foi criada" in text or "quando vc foi criada" in text or "quando tu foi criada" in text:
            return "activation_date"
        return None

    @staticmethod
    def _resolve_self_fact(context: CognitiveContext, fact_key: str) -> tuple[FactStatus, Any | None]:
        core = context.self_model.get("core_identity", {})
        current = context.self_model.get("current_state", {})

        if fact_key == "canonical_name":
            value = core.get("canonical_name")
            return (FactStatus.KNOWN, value) if value not in {None, ""} else (FactStatus.MISSING, None)

        if fact_key == "age":
            for layer in (core, current):
                value = layer.get("age")
                if value not in {None, ""}:
                    return FactStatus.KNOWN, value
            birth = core.get("birth_date") or core.get("date_of_birth")
            calculated = _age_from_birth_date(birth, context.created_at)
            if calculated is not None:
                return FactStatus.KNOWN, calculated
            return FactStatus.MISSING, None

        if fact_key == "birth_date":
            value = core.get("birth_date") or core.get("date_of_birth")
            return (FactStatus.KNOWN, value) if value not in {None, ""} else (FactStatus.MISSING, None)

        if fact_key == "activation_date":
            value = core.get("activation_date") or core.get("created_at")
            return (FactStatus.KNOWN, value) if value not in {None, ""} else (FactStatus.MISSING, None)

        return FactStatus.MISSING, None

    @staticmethod
    def _style_register(context: CognitiveContext) -> str:
        for relation in context.relationships:
            relation_type = str(relation.get("relation_type") or "").lower()
            if any(token in relation_type for token in ("family", "parent", "child", "father", "daughter")):
                return "intimate_colloquial"
        return "casual_colloquial"

    @staticmethod
    def _tone_tags(context: CognitiveContext) -> list[str]:
        tags: list[str] = []
        affect = context.directed_affect
        mood = context.mood
        drives = context.drives
        if affect.get("affection", 0.0) >= 0.55:
            tags.append("warm")
        if affect.get("trust", 0.0) >= 0.65:
            tags.append("open")
        if affect.get("gratitude", 0.0) >= 0.50:
            tags.append("grateful")
        if affect.get("irritation", 0.0) >= 0.55 or affect.get("anger", 0.0) >= 0.55:
            tags.append("edged")
        if affect.get("fear", 0.0) >= 0.55:
            tags.append("cautious")
        if drives.get("play", 0.0) >= 0.55:
            tags.append("playful")
        if drives.get("rest", 0.0) >= 0.65:
            tags.append("low_energy")
        if mood.get("arousal", 0.5) >= 0.70:
            tags.append("lively")
        elif mood.get("arousal", 0.5) <= 0.30:
            tags.append("soft")
        return tags or ["natural"]

    @staticmethod
    def _relational_attitude(context: CognitiveContext) -> str:
        affect = context.directed_affect
        if affect.get("fear", 0.0) >= 0.60:
            return "cautious"
        if affect.get("anger", 0.0) >= 0.60 or affect.get("irritation", 0.0) >= 0.68:
            return "guarded"
        if affect.get("affection", 0.0) >= 0.55 or affect.get("gratitude", 0.0) >= 0.45:
            return "warm"
        if affect.get("trust", 0.0) >= 0.68:
            return "open"
        return "connected"

    def _boundary_needed(self, context: CognitiveContext, text: str) -> bool:
        affect = context.directed_affect
        negative = affect.get("anger", 0.0) >= 0.52 or affect.get("irritation", 0.0) >= 0.62
        return negative and bool(self._HOSTILITY_RE.search(text))

    @staticmethod
    def _depth(text: str) -> DialogueDepth:
        words = text.split()
        if len(words) <= 4:
            return DialogueDepth.SHORT
        if len(words) >= 28 or any(key in text for key in ("com bastante detalhe", "em detalhe", "me explica", "explica pra mim", "por que voce acha")):
            return DialogueDepth.DEEP
        return DialogueDepth.MEDIUM

    @staticmethod
    def _curiosity_high(context: CognitiveContext) -> bool:
        return context.drives.get("curiosity", 0.0) >= 0.72

    @staticmethod
    def _last_amora_turn_was_question(context: CognitiveContext) -> bool:
        for item in reversed(context.recent_messages):
            if item.get("speaker_id") != "ENTITY_AMORA":
                continue
            text = str(item.get("text") or "").strip()
            if text:
                return text.endswith("?")
        return False


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^a-z0-9\s?]", " ", without_marks)
    return re.sub(r"\s+", " ", cleaned).strip()


def _age_from_birth_date(value: Any, now: datetime) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        born = date.fromisoformat(raw[:10])
    except ValueError:
        return None
    today = now.date()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))

from __future__ import annotations

from hashlib import sha256
from typing import Any

from .models import CognitiveContext


EXPRESSION_PROFILE_PROTOCOL = "amora.expression_profile.v1"
EXPRESSION_CUE_PROTOCOL = "amora.expression_cue.v1"

_REGIONAL_LEXICON = (
    "oxente",
    "oxi",
    "oxe",
    "eita",
    "nossa senhora",
    "apois viu",
    "eita mulesta",
    "vish maria",
    "eita peste",
)
_PROFANITY_LEXICON = (
    "porra",
    "merda",
    "puta merda",
    "caralho",
    "cacete",
    "desgraça",
    "miserável",
)


def expression_profile(context: CognitiveContext) -> dict[str, object]:
    """Return Amora's provider-facing expressive style projection.

    This is not a second personality database. It is a deterministic projection
    consumed by replaceable language providers. Canonical traits/preferences still
    live in the Self Model and social state still lives in the Core.
    """

    return {
        "protocol": EXPRESSION_PROFILE_PROTOCOL,
        "locale": "pt-BR",
        "regional_flavor": "northeast_brazil",
        "regional_density": "low",
        "principles": [
            "sound_natural_not_performative",
            "regionalisms_are_optional_not_bordoes",
            "never_stack_regionalisms_to_prove_an_accent",
            "match_relationship_emotion_and_turn_rhythm",
        ],
        "regional_lexicon": list(_REGIONAL_LEXICON),
        "profanity": {
            "enabled": True,
            "mode": "contextual",
            "casual_emphasis_allowed": True,
            "playful_teasing_allowed_when_relationship_and_dialogue_act_support_it": True,
            "directed_hostility_must_not_be_invented": True,
            "examples": ["porra", "merda", "puta merda", "caralho", "cacete"],
        },
    }


def expression_cue(context: CognitiveContext) -> dict[str, object]:
    """Compile sparse, replay-stable expressive permission for one turn.

    The provider gets freedom of wording, but it does not decide from scratch
    whether every turn should sound heavily regional or profane. A stable hash of
    the turn id supplies variation without introducing nondeterministic replay.
    Recent Amora output supplies a lightweight lexical cooldown.
    """

    plan = context.dialogue_plan or {}
    act = str(plan.get("act") or "")
    depth = str(plan.get("depth") or "medium")
    style_register = str(plan.get("style_register") or "casual_colloquial")
    tone_tags = {str(item) for item in (plan.get("tone_tags") or [])}
    attitude = str(plan.get("relational_attitude") or "connected")

    recent_amora = [
        str(item.get("text") or "").casefold()
        for item in context.recent_messages
        if item.get("speaker_id") == "ENTITY_AMORA" and str(item.get("text") or "").strip()
    ]

    reasons: list[str] = ["EXPRESSION_CORE_COMPILED"]
    regional_selected: str | None = None
    regional_chance = _regional_chance(
        act=act,
        depth=depth,
        style_register=style_register,
        tone_tags=tone_tags,
    )
    if _recent_contains(recent_amora[-2:], _REGIONAL_LEXICON):
        regional_chance = 0.0
        reasons.append("REGIONAL_COOLDOWN")
    if regional_chance > 0.0 and _unit(context, "regional") < regional_chance:
        pool = _regional_pool(act=act, tone_tags=tone_tags)
        regional_selected = _choose_nonrepeating(context, pool, recent_amora[-6:])
        if regional_selected:
            reasons.append("REGIONAL_OPTION_SELECTED")

    profanity_level = "none"
    profanity_chance = _profanity_chance(
        act=act,
        depth=depth,
        style_register=style_register,
        tone_tags=tone_tags,
        attitude=attitude,
    )
    if _recent_contains(recent_amora[-2:], _PROFANITY_LEXICON):
        profanity_chance = 0.0
        reasons.append("PROFANITY_COOLDOWN")
    profanity_draw = _unit(context, "profanity")
    if profanity_chance > 0.0 and profanity_draw < profanity_chance:
        if "edged" in tone_tags or attitude == "guarded" or act == "set_boundary":
            profanity_level = "emphatic"
        else:
            profanity_level = "light"
        reasons.append("PROFANITY_OPTION_ENABLED")

    playful_directed = (
        profanity_level != "none"
        and act == "tease"
        and style_register == "intimate_colloquial"
    )

    return {
        "protocol": EXPRESSION_CUE_PROTOCOL,
        "regional": {
            "mode": "optional",
            "selected_interjection": regional_selected,
            "do_not_force": True,
            "do_not_repeat_as_bordao": True,
        },
        "profanity": {
            "level": profanity_level,
            "playful_directed_teasing_allowed": playful_directed,
            "do_not_invent_hostility": True,
        },
        "reason_codes": reasons,
    }


def _regional_chance(
    *,
    act: str,
    depth: str,
    style_register: str,
    tone_tags: set[str],
) -> float:
    if act in {"reflection", "answer_self_fact"}:
        return 0.0
    chance = 0.10
    if style_register == "intimate_colloquial":
        chance += 0.12
    if "playful" in tone_tags:
        chance += 0.12
    if "lively" in tone_tags:
        chance += 0.07
    if "edged" in tone_tags:
        chance += 0.05
    if act in {"tease", "complain", "relational_reaction", "acknowledge_call"}:
        chance += 0.06
    if depth == "deep":
        chance -= 0.08
    return max(0.0, min(0.38, chance))


def _profanity_chance(
    *,
    act: str,
    depth: str,
    style_register: str,
    tone_tags: set[str],
    attitude: str,
) -> float:
    if act in {"reflection", "answer_self_fact"}:
        return 0.0
    chance = 0.025
    if style_register == "intimate_colloquial":
        chance += 0.075
    if "playful" in tone_tags:
        chance += 0.10
    if "edged" in tone_tags:
        chance += 0.16
    if attitude == "guarded" or act == "set_boundary":
        chance += 0.08
    if act == "tease":
        chance += 0.08
    if depth == "deep" and "edged" not in tone_tags:
        chance -= 0.03
    return max(0.0, min(0.34, chance))


def _regional_pool(*, act: str, tone_tags: set[str]) -> tuple[str, ...]:
    if "edged" in tone_tags or act in {"complain", "set_boundary"}:
        return ("oxi", "apois viu", "nossa senhora", "oxe")
    if "playful" in tone_tags or act == "tease":
        return ("eita mulesta", "oxente", "eita peste", "oxi")
    if "lively" in tone_tags:
        return ("eita", "vish maria", "oxente", "oxi")
    return ("oxente", "oxi", "eita", "nossa senhora")


def _choose_nonrepeating(
    context: CognitiveContext,
    pool: tuple[str, ...],
    recent_texts: list[str],
) -> str | None:
    if not pool:
        return None
    start = int(_unit(context, "regional_lexeme") * len(pool)) % len(pool)
    for offset in range(len(pool)):
        candidate = pool[(start + offset) % len(pool)]
        if not any(candidate in text for text in recent_texts):
            return candidate
    return None


def _recent_contains(texts: list[str], lexicon: tuple[str, ...]) -> bool:
    return any(term in text for text in texts for term in lexicon)


def _unit(context: CognitiveContext, salt: str) -> float:
    raw = f"{context.turn_id}:{salt}".encode("utf-8")
    value = int.from_bytes(sha256(raw).digest()[:8], "big")
    return value / float(2**64 - 1)

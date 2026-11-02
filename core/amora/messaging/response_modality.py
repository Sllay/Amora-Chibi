from __future__ import annotations

import hashlib
import re
import unicodedata
from enum import StrEnum
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field

from .models import ChannelType, ExpressionRef, InboundMessage


class ResponseModality(StrEnum):
    TEXT = "text"
    TEXT_EXPRESSION = "text+expression"
    EXPRESSION = "expression"
    REACTION = "reaction"
    SILENCE = "silence"


class ModalityCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    modality: ResponseModality
    utility: float
    components: dict[str, float] = Field(default_factory=dict)
    reason_codes: list[str] = Field(default_factory=list)


class ResponseModalityDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    modality: ResponseModality
    text: str = ""
    expressions: list[ExpressionRef] = Field(default_factory=list, max_length=4)
    candidates: list[ModalityCandidate] = Field(default_factory=list, max_length=8)
    reason_codes: list[str] = Field(default_factory=list, max_length=32)


_SEMANTIC_CLUSTERS: dict[str, frozenset[str]] = {
    "joy": frozenset({"sorriso", "sorrisao", "sorrir", "feliz", "alegre", "alegria", "rir", "risada", "rindo", "kkk", "haha", "engracado", "comedia"}),
    "playful": frozenset({"zoeira", "brinca", "brincar", "brincadeira", "provoca", "provocar", "sapeca", "marota", "meme", "caos", "caotico", "danada"}),
    "affection": frozenset({"amor", "amo", "carinho", "fofo", "fofa", "fofinho", "fofinha", "coracao", "beijo", "abraco", "papis", "papai", "pai"}),
    "celebration": frozenset({"parabens", "conseguiu", "conseguimos", "vitoria", "ganhou", "ganhei", "festa", "comemora", "comemorar", "celebra"}),
    "surprise": frozenset({"nossa", "eita", "oxe", "caramba", "surpresa", "surpreso", "surpresa", "chocado", "chocada", "uau"}),
    "sadness": frozenset({"triste", "tristeza", "chorando", "choro", "chora", "saudade", "desanimo", "desanimado", "desanimada"}),
    "anger": frozenset({"raiva", "brava", "bravo", "irritada", "irritado", "odio", "puta", "puto"}),
    "comfort": frozenset({"calma", "conforta", "conforto", "acolhe", "acolhimento", "apoio", "fica", "aqui"}),
}

_VISUAL_CUES = frozenset({
    "reage", "reagir", "mostra", "mostrar", "expressa", "expressar", "cara", "rostinho",
    "sorriso", "sorrisao", "figurinha", "fig", "sticker", "imagem", "visual",
})
_INFORMATION_CUES = frozenset({
    "como", "qual", "quais", "quando", "onde", "porque", "por", "que", "explica", "explicar",
    "funciona", "funcionar", "erro", "codigo", "código", "log", "status", "integracao", "integração",
})


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in value if not unicodedata.combining(char))


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", _normalize(value)))


def _cluster_tags(tokens: set[str]) -> set[str]:
    return {name for name, lexicon in _SEMANTIC_CLUSTERS.items() if tokens & lexicon}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left | right))


class ResponseModalitySelector:
    """Choose how to answer after semantic dialogue realization.

    This is presentation policy, not autonomous action execution. It may choose a
    transport-native expression only from assets supplied by the trusted adapter
    for the current conversation. Native ids never participate in semantic
    matching and are used only after a local asset has already won.
    """

    EXPRESSION_MIN_FIT = 0.56
    EXPRESSION_ONLY_MIN_FIT = 0.72
    MODALITY_MARGIN = 0.07

    def select(
        self,
        message: InboundMessage,
        response_text: str,
        *,
        emotion_profile: str | None = None,
        recent_expression_ids: Iterable[str] = (),
        prefer_silence: bool = False,
    ) -> ResponseModalityDecision:
        text = response_text.strip()
        user_tokens = _tokens(message.text)
        response_tokens = _tokens(text)
        desired_clusters = _cluster_tags(user_tokens | response_tokens)
        visual_cue = _clamp(len(user_tokens & _VISUAL_CUES) / 2.0)
        question = "?" in message.text
        information_need = _clamp(
            (0.55 if question else 0.0)
            + min(0.45, 0.12 * len(user_tokens & _INFORMATION_CUES))
        )
        short_social = _clamp(1.0 - min(len(message.text.strip()), 180) / 180.0)
        directness = 1.0 if (message.mentions_amora or message.reply_to_amora or message.channel_type == ChannelType.DM) else 0.55
        emotion_tags = _cluster_tags(_tokens(emotion_profile or ""))
        recent = {str(item) for item in recent_expression_ids if str(item)}

        ranked_assets: list[tuple[float, ExpressionRef, dict[str, float]]] = []
        for asset in self._eligible_assets(message):
            semantic = self._asset_semantics(asset)
            semantic_fit = _clamp(0.72 * _overlap(desired_clusters, semantic["clusters"]) + 0.28 * _overlap(user_tokens, semantic["tokens"]))
            social_fit = _clamp(0.65 * _overlap(desired_clusters, semantic["social"]) + 0.35 * visual_cue)
            mood_fit = 0.5 if not emotion_tags else _clamp(0.35 + 0.65 * _overlap(emotion_tags, semantic["clusters"]))
            relationship_fit = 0.72 if directness >= 0.9 else 0.52
            timing = _clamp(0.32 + 0.36 * short_social + 0.22 * visual_cue + 0.10 * directness)
            novelty = 0.0 if asset.native_id in recent else 1.0
            repetition_penalty = 0.28 if asset.native_id in recent else 0.0
            desired_intensity = _clamp(0.35 + 0.25 * visual_cue + 0.20 * min(message.text.count("!"), 2) + 0.20 * short_social)
            intensity = asset.semantic_profile.intensity if asset.semantic_profile is not None else 0.5
            intensity_mismatch = abs(desired_intensity - intensity)
            interruption_cost = _clamp(0.55 * information_need + 0.15 * (1.0 - directness))

            score = _clamp(
                0.31 * semantic_fit
                + 0.16 * social_fit
                + 0.10 * mood_fit
                + 0.10 * relationship_fit
                + 0.14 * timing
                + 0.09 * novelty
                + 0.10 * visual_cue
                - 0.11 * intensity_mismatch
                - 0.13 * interruption_cost
                - repetition_penalty
            )
            ranked_assets.append((score, asset, {
                "semantic_fit": semantic_fit,
                "social_fit": social_fit,
                "mood_fit": mood_fit,
                "relationship_fit": relationship_fit,
                "timing": timing,
                "novelty": novelty,
                "intensity_mismatch": intensity_mismatch,
                "interruption_cost": interruption_cost,
            }))

        ranked_assets.sort(key=lambda row: (-row[0], self._stable_tie(message, row[1])))
        best_score, best_asset, best_components = ranked_assets[0] if ranked_assets else (0.0, None, {})

        text_utility = _clamp(0.58 + 0.28 * information_need + (0.07 if text else -0.58))
        text_expression_utility = -1.0
        expression_utility = -1.0
        if best_asset is not None and best_score >= self.EXPRESSION_MIN_FIT:
            text_expression_utility = _clamp(
                0.30 + 0.62 * best_score + 0.11 * visual_cue + 0.05 * directness - 0.23 * information_need
            )
            expression_utility = _clamp(
                0.18 + 0.72 * best_score + 0.16 * visual_cue + 0.08 * short_social - 0.40 * information_need
            )
            if len(text) > 100:
                expression_utility = _clamp(expression_utility - 0.15)

        reaction_utility = -1.0  # candidate exists architecturally; no trusted executor in this step.
        silence_utility = 0.82 if prefer_silence else (0.18 if not text else 0.02)

        candidates = [
            ModalityCandidate(modality=ResponseModality.TEXT, utility=text_utility, components={"information_need": information_need}, reason_codes=["TEXT_BASELINE"]),
            ModalityCandidate(modality=ResponseModality.TEXT_EXPRESSION, utility=text_expression_utility, components={"asset_fit": best_score, **best_components}, reason_codes=["EXPRESSION_ASSET_AVAILABLE"] if best_asset else ["EXPRESSION_ASSET_UNAVAILABLE"]),
            ModalityCandidate(modality=ResponseModality.EXPRESSION, utility=expression_utility, components={"asset_fit": best_score, **best_components}, reason_codes=["EXPRESSION_ASSET_AVAILABLE"] if best_asset else ["EXPRESSION_ASSET_UNAVAILABLE"]),
            ModalityCandidate(modality=ResponseModality.REACTION, utility=reaction_utility, reason_codes=["REACTION_EXECUTOR_UNAVAILABLE"]),
            ModalityCandidate(modality=ResponseModality.SILENCE, utility=silence_utility, reason_codes=["SILENCE_PREFERENCE"] if prefer_silence else ["SILENCE_LOW_PRIOR"]),
        ]

        chosen = max(candidates, key=lambda item: item.utility)
        if chosen.modality in {ResponseModality.TEXT_EXPRESSION, ResponseModality.EXPRESSION}:
            if best_asset is None or best_score < self.EXPRESSION_MIN_FIT:
                chosen = candidates[0]
            elif chosen.modality == ResponseModality.EXPRESSION and best_score < self.EXPRESSION_ONLY_MIN_FIT:
                chosen = candidates[1] if candidates[1].utility >= text_utility + self.MODALITY_MARGIN else candidates[0]
            elif chosen.utility < text_utility + self.MODALITY_MARGIN:
                chosen = candidates[0]

        if information_need >= 0.75 and chosen.modality == ResponseModality.EXPRESSION:
            chosen = candidates[1] if candidates[1].utility >= text_utility + self.MODALITY_MARGIN else candidates[0]

        output_text = "" if chosen.modality in {ResponseModality.EXPRESSION, ResponseModality.SILENCE} else text
        expressions = [best_asset] if best_asset is not None and chosen.modality in {ResponseModality.TEXT_EXPRESSION, ResponseModality.EXPRESSION} else []
        reasons = [
            "RESPONSE_MODALITY_COMPETED",
            f"RESPONSE_MODALITY_{chosen.modality.value.upper().replace('+', '_').replace('-', '_')}",
        ]
        if expressions:
            reasons.extend(["EXPRESSION_SELECTED_SEMANTICALLY", "NATIVE_ID_NOT_USED_FOR_SELECTION"])
        if information_need >= 0.75:
            reasons.append("INFORMATION_NEED_PROTECTS_TEXT")
        return ResponseModalityDecision(
            modality=chosen.modality,
            text=output_text,
            expressions=expressions,
            candidates=candidates,
            reason_codes=reasons,
        )

    @staticmethod
    def _eligible_assets(message: InboundMessage) -> list[ExpressionRef]:
        result: list[ExpressionRef] = []
        for item in [*message.available_expressions, *message.expressions]:
            if item.kind.value != "sticker":
                continue
            if message.guild_or_group_id and item.guild_or_group_id not in {None, message.guild_or_group_id}:
                continue
            if not any(existing.native_id == item.native_id for existing in result):
                result.append(item)
        return result[:64]

    @staticmethod
    def _asset_semantics(asset: ExpressionRef) -> dict[str, set[str]]:
        profile = asset.semantic_profile
        raw_tokens = _tokens(" ".join(filter(None, [asset.name, asset.description, " ".join(asset.tags)])))
        clusters = _cluster_tags(raw_tokens)
        social = set(clusters)
        if profile is not None:
            profile_tokens = _tokens(" ".join([
                *profile.visual_concepts,
                *profile.affect_tags,
                *profile.social_roles,
                *profile.possible_interpretations,
                profile.register,
            ]))
            raw_tokens |= profile_tokens
            clusters |= set(profile.affect_tags) | _cluster_tags(profile_tokens)
            social |= set(profile.social_roles) | _cluster_tags(profile_tokens)
        return {"tokens": raw_tokens, "clusters": clusters, "social": social}

    @staticmethod
    def _stable_tie(message: InboundMessage, asset: ExpressionRef) -> str:
        raw = f"{message.message_id}:{asset.native_id}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

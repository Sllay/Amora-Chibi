from __future__ import annotations

from .models import ExpressionRef, InboundMessage
from .response_modality import ResponseModalitySelector


class PerceptualResponseModalitySelector(ResponseModalitySelector):
    """Production selector enforcing KNOWN ∩ AVAILABLE for autonomous expressions.

    The base Step-5 selector remains useful for isolated scoring tests and curated
    fixtures. Runtime messaging uses this stricter variant: a guild catalog entry
    cannot be selected merely because Discord says it exists. The expression must
    already have a persistent CPF (``known_to_amora``), or carry a locally curated
    non-transport semantic profile supplied by trusted code/tests.

    A known identity with quarantined semantics (stale/failed/current metadata no
    longer matching the stored fingerprint) remains known, but is not eligible for
    autonomous visual expression until a fresh semantic profile is available.
    """

    @staticmethod
    def _eligible_assets(message: InboundMessage) -> list[ExpressionRef]:
        result: list[ExpressionRef] = []
        # A received sticker proves OBSERVATION, not sendability. The native
        # Discord adapter marks only current-guild catalog assets as available;
        # a DM has no catalog and therefore no sticker send authority.
        for item in message.available_expressions:
            if item.kind.value != "sticker":
                continue
            if (message.channel_type.value != "group"
                    or not message.guild_or_group_id
                    or item.guild_or_group_id != message.guild_or_group_id):
                continue
            curated_profile = (
                item.semantic_profile is not None
                and item.semantic_profile.source != "transport_metadata"
            )
            if not item.known_to_amora and not curated_profile:
                continue
            if item.needs_visual_enrichment and item.semantic_profile is None:
                continue
            if not any(existing.native_id == item.native_id for existing in result):
                result.append(item)
        return result[:64]

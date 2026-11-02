from __future__ import annotations

from collections import deque
from contextvars import ContextVar
from typing import Any

from amora.actions.builtin_capabilities import DISCORD_EXPRESSION_CAPABILITY_ID

from .expression_memory import PerceptualExpressionMemory
from .models import ExpressionKind, InboundMessage, MessagingPlatform, OutboundMessage
from .perceptual_response_modality import PerceptualResponseModalitySelector
from .response_modality import ResponseModalitySelector
from .service import PlatformMessagingService as BasePlatformMessagingService


_CURRENT_MESSAGE: ContextVar[InboundMessage | None] = ContextVar(
    "amora_messaging_current_message",
    default=None,
)


class _CapabilityAwareCognitionProxy:
    """Inject only provider-safe surface affordances into the current request.

    Native expression ids/catalog entries remain transport-local. Cognition only
    learns that a semantic capability is active/available for this exact turn.
    Unknown catalog entries do not count: availability for cognition is the
    intersection of what the transport can send and what Amora already knows.
    """

    def __init__(self, inner: object) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def observe_input(self, request):
        message = _CURRENT_MESSAGE.get()
        if message is not None:
            modalities = ["text", "silence"]
            known_count = self._known_expression_count(message)
            if known_count > 0:
                modalities.extend(["text+expression", "expression"])
                request.metadata["active_capability_ids"] = [DISCORD_EXPRESSION_CAPABILITY_ID]
                request.metadata["surface_available_capability_ids"] = [DISCORD_EXPRESSION_CAPABILITY_ID]
                request.metadata["response_scoped_capability_ids"] = [DISCORD_EXPRESSION_CAPABILITY_ID]
                request.metadata["visual_expression_available"] = True
                request.metadata["visual_expression_count"] = known_count
            else:
                request.metadata["visual_expression_available"] = False
                request.metadata["visual_expression_count"] = 0
            request.metadata["available_response_modalities"] = modalities
        return self._inner.observe_input(request)

    @staticmethod
    def _known_expression_count(message: InboundMessage) -> int:
        if message.platform != MessagingPlatform.DISCORD:
            return 0
        seen: set[str] = set()
        for expression in message.available_expressions:
            if expression.kind != ExpressionKind.STICKER:
                continue
            if message.guild_or_group_id and expression.guild_or_group_id not in {None, message.guild_or_group_id}:
                continue
            curated_profile = (
                expression.semantic_profile is not None
                and expression.semantic_profile.source != "transport_metadata"
            )
            if expression.known_to_amora or curated_profile:
                seen.add(expression.native_id)
        return min(64, len(seen))


class PlatformMessagingService(BasePlatformMessagingService):
    """Step-6 messaging layer: persistent perceptual expression memory.

    The base service remains the authority for attention, timeout, idempotency,
    Conversation Runtime validation, persistence and delivery leases. This layer
    hydrates known expression semantics before cognition, learns only from a
    relevant RESPOND interaction, then performs the Step-5 response-modality
    competition over KNOWN ∩ AVAILABLE expressions.
    """

    _MAX_RECENT_CONVERSATIONS = 512
    _RECENT_EXPRESSIONS_PER_CONVERSATION = 8

    def __init__(self, *args: Any, response_modality: ResponseModalitySelector | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.expression_memory = PerceptualExpressionMemory(self.store, self.clock)
        self.response_modality = response_modality or PerceptualResponseModalitySelector()
        self.cognition = _CapabilityAwareCognitionProxy(self.cognition)
        self._recent_expressions: dict[str, deque[str]] = {}

    async def receive(self, message: InboundMessage):
        hydrated = self.expression_memory.hydrate(message)
        token = _CURRENT_MESSAGE.set(hydrated)
        try:
            return await super().receive(hydrated)
        finally:
            _CURRENT_MESSAGE.reset(token)

    def _queue_outbound(
        self,
        message: InboundMessage,
        decision,
        text: str,
        *,
        metadata: dict[str, object],
        reason_codes: list[str],
    ) -> OutboundMessage:
        # Promotion to KNOWN happens only after the shared attention layer has
        # established that this message deserves a response. A guild catalog by
        # itself never writes perceptual memory.
        message = self.expression_memory.observe_relevant(message, decision)
        state = self.conversation.state() if hasattr(self.conversation, "state") else {}
        emotion_profile = str(state.get("last_emotion_profile") or "") if isinstance(state, dict) else ""
        recent = list(self._recent_expressions.get(self._conversation_key(message), ()))
        modality = self.response_modality.select(
            message,
            text,
            emotion_profile=emotion_profile,
            recent_expression_ids=recent,
        )
        transport_expressions = [item.model_dump(mode="json") for item in modality.expressions]
        safe_scores = {
            candidate.modality.value: round(float(candidate.utility), 4)
            for candidate in modality.candidates
        }
        known_available_count = sum(
            1 for item in message.available_expressions
            if item.kind == ExpressionKind.STICKER and item.known_to_amora
        )
        augmented_metadata = {
            **metadata,
            "response_modality": modality.modality.value,
            "response_modality_reason_codes": list(modality.reason_codes),
            "response_modality_scores": safe_scores,
            "expression_count": len(modality.expressions),
            "known_available_expression_count": min(64, known_available_count),
            "recent_expression_count": len(recent),
            # Delivery-private transport payload. This field is persisted locally
            # and consumed by the worker; it is never copied into CognitiveRequest.
            "transport_expressions": transport_expressions,
        }
        outbound = super()._queue_outbound(
            message,
            decision,
            modality.text,
            metadata=augmented_metadata,
            reason_codes=[*reason_codes, *modality.reason_codes],
        )
        if modality.expressions:
            self._remember_expressions(message, modality.expressions)
            return outbound.model_copy(update={"expressions": list(modality.expressions)})
        return outbound

    @staticmethod
    def _conversation_key(message: InboundMessage) -> str:
        return f"{message.platform.value}:{message.account_scope}:{message.conversation_id}"

    def _remember_expressions(self, message: InboundMessage, expressions) -> None:
        key = self._conversation_key(message)
        history = self._recent_expressions.get(key)
        if history is None:
            if len(self._recent_expressions) >= self._MAX_RECENT_CONVERSATIONS:
                oldest = next(iter(self._recent_expressions), None)
                if oldest is not None:
                    self._recent_expressions.pop(oldest, None)
            history = deque(maxlen=self._RECENT_EXPRESSIONS_PER_CONVERSATION)
            self._recent_expressions[key] = history
        for expression in expressions:
            native_id = str(expression.native_id)
            if native_id:
                history.append(native_id)

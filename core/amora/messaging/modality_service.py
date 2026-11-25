from __future__ import annotations

from collections import deque
import asyncio
import logging
from contextvars import ContextVar
from typing import Any

from amora.actions.builtin_capabilities import DISCORD_EXPRESSION_CAPABILITY_ID

from .expression_memory import PerceptualExpressionMemory
from .models import ExpressionKind, InboundMessage, MessagingPlatform, OutboundMessage
from .perceptual_response_modality import PerceptualResponseModalitySelector
from .response_modality import ResponseModalitySelector
from .service import PlatformMessagingService as BasePlatformMessagingService
from .explicit_memory import is_verified_owner
from .models import ChannelType, RoutingAction
from amora.events import PrivacyScope
from .incoming_vision import unavailable_visual_observation, visual_failure_reason
from .visual_enrichment import VisualExpressionEnrichmentService
from .visual_checkpoint import VisualCheckpointStore, checkpoint_key, contains_transport_input
from .visual_authorization import VISUAL_AUTHORIZATION, require_visual_authorization


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
        self._attach_perceptual_context(request)
        return self._inner.observe_input(request)

    async def observe_input_async(self, request):
        self._attach_perceptual_context(request)
        return await self._inner.observe_input_async(request)

    def _attach_perceptual_context(self, request):
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
            if (message.channel_type == ChannelType.DM
                    and message.platform == MessagingPlatform.DISCORD
                    and message.remote_processing_allowed
                    and request.remote_processing_allowed
                    and request.speaker_id == "PERSON_BK"
                    and request.privacy_scope == PrivacyScope.PERSONAL
                    and not request.metadata.get("test_mode")):
                observed = self._verified_current_stickers(message)
                if observed:
                    # Never send native sticker IDs or CDNs to the text model.
                    # This is an existing enriched CPF, not new pixel inference.
                    request.metadata["known_sticker_perception"] = observed

    @staticmethod
    def _verified_current_stickers(message: InboundMessage) -> list[dict[str, object]]:
        result = []
        for expression in message.expressions:
            profile = expression.semantic_profile
            if (expression.kind != ExpressionKind.STICKER
                    or not expression.known_to_amora
                    or expression.needs_visual_enrichment
                    or expression.semantic_revision < 1
                    or profile is None or profile.source != "visual_perception"):
                continue
            result.append({
                "description": (profile.short_description or "")[:200],
                "visual_concepts": list(profile.visual_concepts[:8]),
                "affect_tags": list(profile.affect_tags[:6]),
                "social_roles": list(profile.social_roles[:6]),
                "interpretations": list(profile.possible_interpretations[:6]),
                "confidence": round(profile.confidence, 3),
                "source": "verified_perceptual_memory",
            })
            if len(result) >= 3:
                break
        return result

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

    def __init__(self, *args: Any, response_modality: ResponseModalitySelector | None = None,
                 incoming_vision=None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.expression_memory = PerceptualExpressionMemory(self.store, self.clock)
        self.visual_enrichment = VisualExpressionEnrichmentService(self.expression_memory)
        self.incoming_vision = incoming_vision
        self.visual_checkpoints = VisualCheckpointStore(self.store.database, self.clock)
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

    @staticmethod
    def _cognitive_text(message: InboundMessage) -> str:
        # A previously enriched and currently matched sticker is no longer an
        # opaque attachment. Its meaning is supplied separately as evidence.
        if (not message.text.strip()
                and _CapabilityAwareCognitionProxy._verified_current_stickers(message)
                and message.channel_type == ChannelType.DM
                and message.remote_processing_allowed):
            return "[Usuário enviou uma figurinha cujo significado visual já foi aprendido.]"
        return BasePlatformMessagingService._cognitive_text(message)

    @staticmethod
    def _has_visual_input(message):
        return bool(message.incoming_image or message.incoming_images) or any(
            a.media_type and a.media_type.lower().split(";", 1)[0].startswith("image/")
            for a in message.attachments
        ) or (
            message.incoming_image_status == "IMAGE_UNSUPPORTED_FORMAT"
            and bool(message.attachments)
        )

    async def _incoming_visual_observation(self, message, decision):
        # Text/sticker/audio-only turns never enter the visual storage or
        # authorization path. Inspect only local transport metadata here;
        # all visual inputs still pass the full live authorization fence.
        if not self._has_visual_input(message):
            return None
        if not (message.platform == MessagingPlatform.DISCORD
                and message.channel_type == ChannelType.DM
                and message.privacy_scope == PrivacyScope.PERSONAL
                and message.remote_processing_allowed
                and decision.action == RoutingAction.RESPOND
                and is_verified_owner(self.attention.profiles, message, decision.speaker_entity_id)):
            return None
        async def guard():
            if not (message.remote_processing_allowed and message.privacy_scope == PrivacyScope.PERSONAL
                    and is_verified_owner(self.attention.profiles, message, decision.speaker_entity_id)):
                return False
            try:
                return await self._visual_checkpoint_call(message, "source_current",
                    self._session_ref(message), self._hash(message.native_source_message_id),
                    message.transport_revision)
            except Exception:
                # An unverifiable revision is not permission to send pixels.
                return False
        token = VISUAL_AUTHORIZATION.set(guard)
        try:
            if not await guard():
                return None
            if message.incoming_images:
                result = await self._visual_batch(message, decision)
            else:
                result = await self._visual_single(message)
            return result if await guard() else None
        finally:
            VISUAL_AUTHORIZATION.reset(token)

    async def _visual_checkpoint_call(self, message, operation, *args):
        if self._use_storage_lane(message):
            return await self.storage_lane.call("visual_checkpoint", operation, *args)
        return getattr(self.visual_checkpoints, operation)(*args)

    async def _checkpointed_visual_analysis(self, message):
        import os
        from amora.cognition.visual_context import visual_context_for_request
        from .incoming_vision import valid_image_bytes
        image = message.incoming_image
        await require_visual_authorization()
        if os.getenv("AMORA_DISCORD_IMAGE_VISION", "1").strip().lower() in {"0", "false", "off", "no"}:
            return unavailable_visual_observation("unconfigured")
        valid_image_bytes(image)
        session = self._session_ref(message)
        native_hash = self._hash(message.native_source_message_id)
        revision = message.transport_revision
        key = checkpoint_key(session, self._turn_id(message), native_hash, revision, image, message.text)
        args = (session, native_hash, revision, key)
        try:
            cached = await self._visual_checkpoint_call(message, "load", *args)
        except Exception:
            cached = None
        await require_visual_authorization()
        if cached is not None and not contains_transport_input(cached, image):
            logging.getLogger("amora.incoming_vision").info("visual_checkpoint_hit")
            return cached
        if self.incoming_vision is None or not self.incoming_vision.available:
            return unavailable_visual_observation("unconfigured")
        result = await self.incoming_vision.analyze(image, message.text)
        await require_visual_authorization()
        validated = visual_context_for_request(result, remote=True, private=True, mode="reply")
        if validated is None or contains_transport_input(validated, image):
            return unavailable_visual_observation("invalid_observation")
        if validated["status"] == "analyzed":
            try:
                await self._visual_checkpoint_call(message, "save", *args, validated)
            except Exception:
                logging.getLogger("amora.incoming_vision").warning("visual_checkpoint_write_failed")
        await require_visual_authorization()
        return validated


    async def _visual_batch(self, message, decision):
        from amora.cognition.visual_context import visual_context_for_request
        # Attachment order is the only numbering authority. Include skipped
        # native images, not non-image documents in the count.
        supplied = {item.attachment_id: item for item in message.incoming_images}
        refs = [a for a in message.attachments if a.attachment_id in supplied
                or (a.media_type or '').lower().split(';', 1)[0].startswith('image/')]
        count = len(refs)
        if count < 2:
            return unavailable_visual_observation('invalid_observation')
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 27
        observations = []
        for index, ref in enumerate(refs[:3], 1):
            item = supplied.get(ref.attachment_id)
            # Do not turn a cached owner decision into authorization for a
            # subsequent image when a binding has since been revoked.
            if not (message.remote_processing_allowed and message.privacy_scope == PrivacyScope.PERSONAL
                    and is_verified_owner(self.attention.profiles, message, decision.speaker_entity_id)):
                return None
            remaining = deadline - loop.time()
            if item is None:
                result = unavailable_visual_observation('processing_failed')
            elif remaining <= 0:
                result = unavailable_visual_observation('timeout')
            else:
                single = message.model_copy(update={'incoming_images': [], 'attachments': [ref],
                    'incoming_image': item.image, 'incoming_image_status': item.ingest_status})
                try:
                    # Reserve a fair share of the existing 27-second budget.
                    result = await asyncio.wait_for(self._visual_single(single),
                        timeout=remaining / (min(3, count) - index + 1))
                except TimeoutError:
                    result = unavailable_visual_observation('timeout')
                result = visual_context_for_request(result, remote=True, private=True, mode='reply')
                if result is None:
                    result = unavailable_visual_observation('invalid_observation')
            observations.append({'image_number': index, 'observation': result})
        if not is_verified_owner(self.attention.profiles, message, decision.speaker_entity_id):
            return None
        analyzed = sum(row['observation']['status'] == 'analyzed' for row in observations)
        return {'observation_version': 3, 'source': 'vision_batch', 'image_count': count,
                'images': observations, 'status': 'analyzed' if analyzed == count else
                'partial' if analyzed else 'unavailable'}

    async def _visual_single(self, message):
        if not self._has_visual_input(message):
            return None

        # The transport status is a machine code, never a source of image
        # observations or conversation instructions. Preserve only a fixed
        # failure category so Amora can admit a failed image read naturally.
        ingest_status = message.incoming_image_status or ""
        if ingest_status in {
            "IMAGE_NATIVE_TOO_LARGE", "IMAGE_TOO_LARGE",
            "IMAGE_TRANSFORM_SIZE_EXCEEDED", "IMAGE_TRANSPORT_TOO_LARGE",
        }:
            failure_reason = "too_large"
        elif ingest_status == "IMAGE_UNSUPPORTED_FORMAT":
            failure_reason = "unsupported_format"
        elif ingest_status.startswith("IMAGE_TRANSFORM_"):
            failure_reason = "processing_failed"
        elif ingest_status.startswith(("IMAGE_CDN_", "IMAGE_FETCH_", "IMAGE_URL_")):
            failure_reason = "download_failed"
        elif message.incoming_image is None and all(
            a.media_type and a.media_type.lower().split(";", 1)[0]
            not in {"image/png", "image/jpeg", "image/webp"}
            for a in message.attachments if a.media_type
            and a.media_type.lower().split(";", 1)[0].startswith("image/")
        ):
            failure_reason = "unsupported_format"
        else:
            failure_reason = "processing_failed"
        if message.incoming_image is None and (self.incoming_vision is None or not self.incoming_vision.available):
            logging.getLogger("amora.incoming_vision").info(
                "incoming_image_unavailable reason=unconfigured"
            )
            return unavailable_visual_observation("unconfigured")
        if message.incoming_image is None:
            logging.getLogger("amora.incoming_vision").info(
                "incoming_image_unavailable reason=%s", failure_reason,
            )
            return unavailable_visual_observation(failure_reason)
        vision_started = asyncio.get_running_loop().time()
        try:
            # Inbound timeout includes this analysis; avoid blocking social reply
            # for a model outage. Never fall back to imagined pixels.
            result = await asyncio.wait_for(
                self._checkpointed_visual_analysis(message),
                timeout=27,
            )
            logging.getLogger("amora.incoming_vision").info(
                "incoming_image_analysis_result outcome=%s elapsed_ms=%d",
                result.get("status", "unknown"),
                max(0, round((asyncio.get_running_loop().time() - vision_started) * 1000)),
            )
            return result
        except Exception as exc:
            logging.getLogger("amora.incoming_vision").warning(
                "incoming_image_analysis_failed code=%s elapsed_ms=%d",
                visual_failure_reason(exc),
                max(0, round((asyncio.get_running_loop().time() - vision_started) * 1000)),
            )
            return unavailable_visual_observation(visual_failure_reason(exc))

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
        self.visual_enrichment.observe_policy(message, decision)
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

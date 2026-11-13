from __future__ import annotations

from typing import Any
import re

from amora.autonomy import AutonomyEngine
from amora.emotion import EmotionEngine
from amora.events import PrivacyScope
from amora.identity import EntityRegistry
from amora.language import ACIRCompiler, PerspectiveFrame, PerspectiveResolver, PerspectiveStack
from amora.language.text_references import scoped_registry, history_antecedents
from amora.memory import MemoryQuery, MemoryService
from amora.social import SocialSceneTracker
from amora.time import Clock
from amora.world import SelfModelService, WorldModelService
from amora.world.relationship_learning import RelationshipLearningService
from amora.world.service import RelationshipEvolutionService

from .dialogue import DialogueOrchestrator
from .models import CognitiveContext, CognitiveRequest, ContextMemory
from .store import CognitiveStore


def resolve_scene_visibility(request: CognitiveRequest) -> str:
    """Use trusted transport metadata; unknown messaging audiences are shared."""
    if request.metadata.get("surface") == "platform_messaging":
        return "private" if request.metadata.get("channel_type") == "dm" else "group"
    if request.privacy_scope == PrivacyScope.PUBLIC:
        return "group"
    return "private"


class ProviderPrivacyGate:
    @staticmethod
    def remote_allowed(request: CognitiveRequest) -> bool:
        return request.remote_processing_allowed and request.privacy_scope not in {PrivacyScope.AUTH, PrivacyScope.SECRET}

    @staticmethod
    def memory_allowed(memory, *, remote: bool, recipient_id: str | None = None) -> bool:
        """Protect ownership/audience, independently of the topic's vocabulary.

        Remote-processing consent is not a license to disclose a memory to a
        different participant. Unowned PERSONAL memories without an identified
        provenance actor are withheld until their audience can be established.
        """
        if memory.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
            return False
        if remote and not memory.remote_processing_allowed:
            return False
        if memory.privacy_scope == PrivacyScope.PUBLIC:
            return True

        audience = set(getattr(memory, "audience", ()) or ())
        owner = getattr(memory, "owner_entity_id", None)
        actor = getattr(getattr(memory, "provenance", None), "actor_id", None)
        if not recipient_id:
            return False
        if owner == recipient_id or recipient_id in audience:
            return True
        if owner or audience:
            return False
        return actor == recipient_id

    @staticmethod
    def claim_allowed(claim, *, remote: bool, recipient_id: str | None = None) -> bool:
        """Nonpublic world facts are not exported as gossip about third parties."""
        if claim.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
            return False
        if remote and not claim.remote_processing_allowed:
            return False
        if claim.privacy_scope == PrivacyScope.PUBLIC:
            return True
        if not recipient_id:
            return False
        if claim.subject_id not in {recipient_id, "ENTITY_AMORA"}:
            return False
        source = getattr(claim, "source_entity_id", None)
        return source in {None, recipient_id, "ENTITY_AMORA"}

    @staticmethod
    def shared_history_allowed(message, request: CognitiveRequest) -> bool:
        # A group session is not a DM session. Preserve only explicitly
        # group-origin personal messages from the same transport session.
        if message.privacy_scope == PrivacyScope.PUBLIC:
            return True
        return (
            message.privacy_scope == PrivacyScope.PERSONAL
            and request.metadata.get("surface") == "platform_messaging"
            and request.metadata.get("channel_type") == "group"
            and bool(request.session_id)
            and message.session_id == request.session_id
            and message.metadata.get("surface") == "platform_messaging"
            and message.metadata.get("channel_type") == "group"
        )

    @staticmethod
    def message_allowed(message, *, remote: bool, recipient_id: str) -> bool:
        """One shared session is not permission to share another person's DM."""
        if message.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
            return False
        if remote and not message.remote_processing_allowed:
            return False
        if message.privacy_scope == PrivacyScope.PUBLIC:
            return True
        return (
            message.speaker_id == recipient_id
            or (
                message.speaker_id == "ENTITY_AMORA"
                and message.addressee_id == recipient_id
            )
        )


class ContextCompiler:
    def __init__(self, *, clock: Clock, identities: EntityRegistry, memory: MemoryService, world: WorldModelService,
                 self_model: SelfModelService, emotion: EmotionEngine, autonomy: AutonomyEngine,
                 social: SocialSceneTracker, store: CognitiveStore, privacy_gate: ProviderPrivacyGate | None = None,
                 dialogue: DialogueOrchestrator | None = None, family_authority_ids: frozenset[str] | None = None) -> None:
        self.clock = clock
        self.identities = identities
        self.memory = memory
        self.world = world
        self.self_model = self_model
        self.emotion = emotion
        self.autonomy = autonomy
        self.social = social
        self.store = store
        self.privacy_gate = privacy_gate or ProviderPrivacyGate()
        self.dialogue = dialogue or DialogueOrchestrator()
        self.acir = ACIRCompiler(PerspectiveResolver(identities))
        self.relationship_learning = RelationshipLearningService(
            world=world, relationships=RelationshipEvolutionService(world.store, clock, identities),
            identities=identities, cognitive_store=store, clock=clock, family_authority_ids=family_authority_ids,
        )
        world.subscribe_claim_observer(self.relationship_learning.observe_claim)

    def compile(self, request: CognitiveRequest, *, token_budget: int = 16384) -> CognitiveContext:
        from .expressive_profile import configured_profile, profile_text
        remote = self.privacy_gate.remote_allowed(request)
        visibility = resolve_scene_visibility(request)
        # Durable extraction has one observed source, no social task and no
        # authority to infer state from unrelated history. Compile only that source.
        if (request.metadata.get("cognitive_mode") == "reflection"
                and request.metadata.get("durable_memory_reflection")):
            from .reflection_contract import MEMORY_ONLY_REASON
            return CognitiveContext(
                turn_id=request.turn_id, created_at=self.clock.now(),
                speaker_id=request.speaker_id, target_id=request.target_id,
                user_text=request.text, privacy_scope=request.privacy_scope,
                remote_processing_allowed=remote, scene_visibility=visibility,
                token_budget=token_budget, expression_profile="core-v1",
                reason_codes=["CANONICAL_STATE_OUTSIDE_PROVIDER", "COGNITIVE_REFLECTION",
                              MEMORY_ONLY_REASON,
                              "REMOTE_CONTEXT_ALLOWED" if remote else "REMOTE_CONTEXT_BLOCKED"],
            )
        profile = configured_profile()
        profile_text(profile)  # integrity and configuration validated before provider work
        shared = visibility != "private"
        # The complete opt-in profile is not an audience-labelled data source.
        # Shared turns use the existing public Core expression projection.
        configured = profile
        if shared:
            profile = "core-v1"
        reasons = ["CANONICAL_STATE_OUTSIDE_PROVIDER", "REMOTE_CONTEXT_ALLOWED" if remote else "REMOTE_CONTEXT_BLOCKED"]
        if shared and configured != profile:
            reasons.append("SHARED_PROFILE_ISOLATED")
        if bool(request.metadata.get("test_mode", False)):
            reasons.append("TEST_MODE_ACTIVE")

        mode = request.metadata.get("cognitive_mode")
        if mode == "reflection":
            reasons.append("COGNITIVE_REFLECTION")
        elif mode == "autonomous_initiation":
            reasons.append("AUTONOMOUS_INITIATION")
        acir: dict[str, Any] | None = None
        compile_acir = request.privacy_scope not in {PrivacyScope.AUTH, PrivacyScope.SECRET} and mode != "autonomous_initiation"
        if not compile_acir:
            reasons.append("ACIR_REDACTED_PRIVATE_SCOPE")

        speaker = self.identities.get(request.speaker_id)
        speaker_identity: dict[str, Any] | None = None
        if speaker is not None:
            speaker_identity = {
                "entity_id": speaker.id,
                "canonical_name": speaker.canonical_name,
                "entity_type": speaker.entity_type.value,
            }
            reasons.append("SPEAKER_CANONICAL_IDENTITY_RESOLVED")
        else:
            reasons.append("SPEAKER_CANONICAL_IDENTITY_UNKNOWN")

        mood = self.emotion.current_mood()
        affect: dict[str, float] = {}
        if speaker is not None:
            directed = self.emotion.affect_for(request.speaker_id)
            affect = {"anger": directed.anger, "affection": directed.affection, "trust": directed.trust,
                      "irritation": directed.irritation, "fear": directed.fear, "gratitude": directed.gratitude}

        drives = {item.drive.value: round(item.need, 6) for item in self.autonomy.current_drives()}
        current_intention = self.autonomy.store.current_intention()
        intention = current_intention.model_dump(mode="json") if current_intention is not None else None
        entity_ids = {request.speaker_id}
        if request.target_id:
            entity_ids.add(request.target_id)

        memories: list[ContextMemory] = []
        # Generic recollection requests contain no event-specific retrieval terms.
        # Use entity/recency ranking rather than letting conversational filler
        # accidentally match arbitrary memories via full-text search.
        recall_text = request.text
        from unicodedata import combining, normalize
        normalized_recall = "".join(
            char for char in normalize("NFKD", request.text.casefold())
            if not combining(char)
        )
        normalized_recall = " ".join(normalized_recall.strip(" ?!.,").split())
        if normalized_recall.startswith("amora, "):
            normalized_recall = normalized_recall[len("amora, "):]
        if normalized_recall in {
            "voce lembra do que conversamos",
            "lembra do que conversamos",
            "voce lembra de mim",
            "lembra de mim",
            "o que voce lembra de mim",
            "o que voce lembra sobre mim",
            "o que voce lembra das nossas conversas",
        }:
            recall_text = ""
            reasons.append("GENERIC_RECOLLECTION_ENTITY_RECALL")
        explicit_recall = bool(re.search(
            r"\b(?:lembra|lembras|lembrar|recorda|recordar|recordas)\b|"
            r"\b(?:que|qual)\b.{0,80}\b(?:falei|contei|disse|comentei)\b",
            normalized_recall,
        ))
        for hit in self.memory.recall(MemoryQuery(text=recall_text, entity_ids=entity_ids, limit=8, mmr_lambda=0.78,
                                                 projection_recipient_id=request.speaker_id,
                                                 projection_public_only=shared, projection_remote_only=remote,
                                                 allow_entity_fallback=explicit_recall and not shared)):
            item = hit.memory
            if shared and item.privacy_scope != PrivacyScope.PUBLIC:
                continue
            if self.privacy_gate.memory_allowed(item, remote=remote, recipient_id=request.speaker_id):
                memories.append(ContextMemory(id=str(item.id), kind=item.kind.value, content=item.summary or item.content,
                                              score=hit.score, confidence=item.provenance.confidence,
                                              privacy_scope=item.privacy_scope.value, reason_codes=hit.reason_codes))

        claims: list[dict[str, Any]] = []
        authorized_relations: set[tuple[str, str, str]] = set()
        for claim in self.world.current_state():
            if shared and claim.privacy_scope != PrivacyScope.PUBLIC:
                continue
            if not self.privacy_gate.claim_allowed(claim, remote=remote, recipient_id=request.speaker_id):
                continue
            if (claim.canonical and claim.subject_id and isinstance(claim.object_value, str)
                    and claim.predicate in {"mother_of", "father_of", "child_of"}):
                authorized_relations.add((claim.subject_id, claim.predicate, claim.object_value))
            if claim.subject_id not in entity_ids | {"ENTITY_AMORA", None} and len(claims) >= 4:
                continue
            claims.append({"id": str(claim.id), "kind": claim.kind.value, "statement": claim.statement,
                           "subject_id": claim.subject_id, "predicate": claim.predicate, "object": claim.object_value,
                           "confidence": claim.confidence, "canonical": claim.canonical})
            if len(claims) >= 10:
                break

        persisted_messages = self.store.list_messages(limit=96, session_id=request.session_id, exclude_invalidated=True)
        reasons.append("RECENT_HISTORY_SESSION_SCOPED" if request.session_id is not None else "RECENT_HISTORY_UNSCOPED_LEGACY")
        test_turn_ids = {str(message.turn_id) for message in persisted_messages if bool(message.metadata.get("test_mode", False))}
        include_session_tests = bool(request.metadata.get("test_mode", False)) and request.session_id is not None
        if test_turn_ids:
            reasons.append("TEST_SESSION_CONTEXT_ALLOWED" if include_session_tests else "TEST_TURNS_EXCLUDED_FROM_RECENT")
        recent: list[dict[str, Any]] = []
        for message in persisted_messages:
            if (str(message.turn_id) == str(request.turn_id)
                    or (str(message.turn_id) in test_turn_ids and not include_session_tests)):
                continue
            if shared:
                if not self.privacy_gate.shared_history_allowed(message, request):
                    continue
                if remote and not message.remote_processing_allowed:
                    continue
            elif not self.privacy_gate.message_allowed(
                message, remote=remote, recipient_id=request.speaker_id
            ):
                continue
            text = message.spoken_text or message.normalized_text or message.raw_text
            if text:
                recent.append({"turn_id": str(message.turn_id), "speaker_id": message.speaker_id,
                               "addressee_id": message.addressee_id, "text": text[:800],
                               "source": message.source.value, "status": message.status.value})
        recent = recent[-48:]
        reasons.append("CURRENT_TURN_EXCLUDED_FROM_RECENT")

        reference_registry = scoped_registry(self.identities, request.speaker_id,
                                             authorized_relations=authorized_relations,
                                             include_recipient_edges=not shared)
        relationships = [relation.model_dump(mode="json") for relation in reference_registry.relationships]
        if compile_acir:
            resolver = PerspectiveResolver(reference_registry)
            perspective = PerspectiveStack(PerspectiveFrame(speaker_id=request.speaker_id, addressee_id=request.target_id))
            antecedents = history_antecedents(recent, resolver) if request.session_id is not None else []
            acir = ACIRCompiler(resolver).compile_utterance(request.text, perspective,
                                                         antecedents=antecedents).model_dump(mode="json")
            reasons.append("ACIR_COMPILED")

        self_snapshot = self.self_model.snapshot()
        if shared:
            # Snapshot values have no audience metadata. Do not project learned
            # preferences, relationship maps or private current-state summaries.
            identity = self_snapshot.get("core_identity", {})
            self_snapshot = {"core_identity": {
                key: identity[key] for key in ("canonical_name",) if key in identity
            }}
            intention = None
            reasons.append("SHARED_CONTEXT_SELF_MODEL_RESTRICTED")
        if remote:
            # Cross-person self-relationship maps are not safe to expose
            # wholesale. Recipient-scoped relationships are projected above.
            allowed_layers = {"core_identity", "traits", "preferences", "current_state"}
            self_snapshot = {key: value for key, value in self_snapshot.items() if key in allowed_layers}
            reasons.append("SELF_MODEL_REMOTE_LAYER_ALLOWLIST")

        context = CognitiveContext(
            turn_id=request.turn_id, created_at=self.clock.now(), speaker_id=request.speaker_id, expression_profile=profile,
            target_id=request.target_id, user_text=request.text, acir=acir, self_model=self_snapshot,
            relationships=relationships, mood={"valence": round(mood.valence, 6), "arousal": round(mood.arousal, 6),
                                               "dominance": round(mood.dominance, 6)},
            directed_affect=affect, drives=drives, active_intention=intention, memories=memories,
            world_claims=claims, recent_messages=recent, privacy_scope=request.privacy_scope,
            remote_processing_allowed=remote, token_budget=token_budget, reason_codes=reasons,
            scene_visibility=visibility,
        )
        # Trusted speaker identity is application-generated metadata. It contains
        # no platform handle/snowflake and therefore gives providers the canonical
        # person/name without leaking the external account identifier.
        if speaker_identity is not None:
            context.dialogue_plan["speaker_identity"] = speaker_identity
        dialogue_plan = self.dialogue.plan(context)
        context.dialogue_plan = dialogue_plan.model_dump(mode="json")
        if speaker_identity is not None:
            context.dialogue_plan["speaker_identity"] = speaker_identity
        if context.acir is not None:
            context.dialogue_plan["input_acir"] = context.acir
            context.reason_codes.append("ACIR_BOUND_TO_DIALOGUE_PLAN")
        context.reason_codes.extend(code for code in dialogue_plan.reason_codes if code not in context.reason_codes)
        context.reason_codes.append(f"DIALOGUE_ACT_{dialogue_plan.act.value.upper()}")
        return self._fit_budget(context, self.dialogue)

    @staticmethod
    def _fit_budget(context: CognitiveContext, dialogue=None) -> CognitiveContext:
        from .context_budget import estimate_context, ContextBudgetExceeded
        def replan():
            identity = context.dialogue_plan.get("speaker_identity")
            old = set(context.dialogue_plan.get("reason_codes") or ())
            context.reason_codes = [code for code in context.reason_codes
                                    if code not in old and not code.startswith("DIALOGUE_ACT_")]
            context.dialogue_plan = (dialogue or DialogueOrchestrator()).plan(context).model_dump(mode="json")
            context.reason_codes.extend(context.dialogue_plan.get("reason_codes") or ())
            context.reason_codes.append("DIALOGUE_ACT_" + context.dialogue_plan["act"].upper())
            if identity:
                context.dialogue_plan["speaker_identity"] = identity
            if context.acir is not None:
                context.dialogue_plan["input_acir"] = context.acir
        # Relevance-ranked memories and the newest causal exchange are retained.
        # Drop optional projections before removing old complete exchanges.
        replan()
        while estimate_context(context) > context.token_budget:
            if context.capabilities:
                context.capabilities.pop()
                reason = "CONTEXT_TRIMMED_CAPABILITY"
            elif len(context.world_claims) > 2:
                context.world_claims.pop()
                reason = "CONTEXT_TRIMMED_WORLD"
            elif len(context.memories) > 2:
                context.memories.pop()
                reason = "CONTEXT_TRIMMED_MEMORY"
            elif len(context.recent_messages) > 2:
                first = context.recent_messages.pop(0)
                while len(context.recent_messages) > 2 and context.recent_messages[0].get("turn_id") == first.get("turn_id") and first.get("turn_id"):
                    context.recent_messages.pop(0)
                reason = "CONTEXT_TRIMMED_RECENT"
            else:
                raise ContextBudgetExceeded(estimate_context(context), context.token_budget)
            if reason not in context.reason_codes:
                context.reason_codes.append(reason)
            replan()
        replan()
        return context

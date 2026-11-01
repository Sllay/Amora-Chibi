from __future__ import annotations

from typing import Any

from amora.autonomy import AutonomyEngine
from amora.emotion import EmotionEngine
from amora.events import PrivacyScope
from amora.identity import EntityRegistry
from amora.language import ACIRCompiler, PerspectiveFrame, PerspectiveResolver, PerspectiveStack
from amora.memory import MemoryQuery, MemoryService
from amora.social import SocialSceneTracker
from amora.time import Clock
from amora.world import SelfModelService, WorldModelService
from amora.world.relationship_learning import RelationshipLearningService
from amora.world.service import RelationshipEvolutionService

from .dialogue import DialogueOrchestrator
from .models import CognitiveContext, CognitiveRequest, ContextMemory
from .store import CognitiveStore


class ProviderPrivacyGate:
    """Filters canonical context before it leaves the A15.

    Disclosure and remote processing are deliberately separate. AUTH/SECRET are
    never eligible for a remote provider. Item-level remote flags remain binding.
    """

    @staticmethod
    def remote_allowed(request: CognitiveRequest) -> bool:
        return (
            request.remote_processing_allowed
            and request.privacy_scope not in {PrivacyScope.AUTH, PrivacyScope.SECRET}
        )

    @staticmethod
    def memory_allowed(memory, *, remote: bool) -> bool:
        if memory.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
            return False
        if remote and not memory.remote_processing_allowed:
            return False
        return True

    @staticmethod
    def claim_allowed(claim, *, remote: bool) -> bool:
        if claim.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
            return False
        if remote and not claim.remote_processing_allowed:
            return False
        return True


class ContextCompiler:
    def __init__(
        self,
        *,
        clock: Clock,
        identities: EntityRegistry,
        memory: MemoryService,
        world: WorldModelService,
        self_model: SelfModelService,
        emotion: EmotionEngine,
        autonomy: AutonomyEngine,
        social: SocialSceneTracker,
        store: CognitiveStore,
        privacy_gate: ProviderPrivacyGate | None = None,
        dialogue: DialogueOrchestrator | None = None,
        family_authority_ids: frozenset[str] | None = None,
    ) -> None:
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
        # ACIR is compiled from the final CognitiveRequest participants, after the
        # social/conversation layer has resolved speaker and addressee. The
        # compiler is stateless; sharing the canonical EntityRegistry keeps it in
        # lockstep with core.perspective/core.acir without platform-specific wiring.
        self.acir = ACIRCompiler(PerspectiveResolver(identities))

        # Relationship learning is a derived local observer of already-gated
        # WorldModel hypotheses. It never consumes provider text directly and it
        # never owns affect/trust; those remain EmotionEngine state. Parenthood
        # authority is projected from operator-owned people profiles rather than
        # being inferred from literal conversation text.
        self.relationship_learning = RelationshipLearningService(
            world=world,
            relationships=RelationshipEvolutionService(world.store, clock, identities),
            identities=identities,
            cognitive_store=store,
            clock=clock,
            family_authority_ids=family_authority_ids,
        )
        world.subscribe_claim_observer(self.relationship_learning.observe_claim)

    def compile(self, request: CognitiveRequest, *, token_budget: int = 3200) -> CognitiveContext:
        remote = self.privacy_gate.remote_allowed(request)
        reasons = ["CANONICAL_STATE_OUTSIDE_PROVIDER"]
        reasons.append("REMOTE_CONTEXT_ALLOWED" if remote else "REMOTE_CONTEXT_BLOCKED")
        if bool(request.metadata.get("test_mode", False)):
            reasons.append("TEST_MODE_ACTIVE")

        acir: dict[str, Any] | None = None
        if request.privacy_scope not in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
            perspective = PerspectiveStack(
                PerspectiveFrame(
                    speaker_id=request.speaker_id,
                    addressee_id=request.target_id,
                )
            )
            acir = self.acir.compile_utterance(request.text, perspective).model_dump(mode="json")
            reasons.append("ACIR_COMPILED")
        else:
            reasons.append("ACIR_REDACTED_PRIVATE_SCOPE")

        mood = self.emotion.current_mood()
        affect: dict[str, float] = {}
        if self.identities.get(request.speaker_id) is not None:
            directed = self.emotion.affect_for(request.speaker_id)
            affect = {
                "anger": directed.anger,
                "affection": directed.affection,
                "trust": directed.trust,
                "irritation": directed.irritation,
                "fear": directed.fear,
                "gratitude": directed.gratitude,
            }

        drives = {item.drive.value: round(item.need, 6) for item in self.autonomy.current_drives()}
        current_intention = self.autonomy.store.current_intention()
        intention = current_intention.model_dump(mode="json") if current_intention is not None else None

        entity_ids = {request.speaker_id}
        if request.target_id:
            entity_ids.add(request.target_id)
        memory_hits = self.memory.recall(
            MemoryQuery(text=request.text, entity_ids=entity_ids, limit=8, mmr_lambda=0.78)
        )
        memories: list[ContextMemory] = []
        for hit in memory_hits:
            item = hit.memory
            if not self.privacy_gate.memory_allowed(item, remote=remote):
                continue
            memories.append(
                ContextMemory(
                    id=str(item.id),
                    kind=item.kind.value,
                    content=item.summary or item.content,
                    score=hit.score,
                    confidence=item.provenance.confidence,
                    privacy_scope=item.privacy_scope.value,
                    reason_codes=hit.reason_codes,
                )
            )

        claims: list[dict[str, Any]] = []
        for claim in self.world.current_state():
            if not self.privacy_gate.claim_allowed(claim, remote=remote):
                continue
            if claim.subject_id not in entity_ids | {"ENTITY_AMORA", None} and len(claims) >= 4:
                continue
            claims.append(
                {
                    "id": str(claim.id),
                    "kind": claim.kind.value,
                    "statement": claim.statement,
                    "subject_id": claim.subject_id,
                    "predicate": claim.predicate,
                    "object": claim.object_value,
                    "confidence": claim.confidence,
                    "canonical": claim.canonical,
                }
            )
            if len(claims) >= 10:
                break

        # Literal transcript is local to one conversation session. Cross-session
        # continuity must come through canonical Memory/World state, never by
        # silently copying raw Web/DM/channel turns into another surface.
        persisted_messages = self.store.list_messages(limit=24, session_id=request.session_id)
        if request.session_id is not None:
            reasons.append("RECENT_HISTORY_SESSION_SCOPED")
        else:
            reasons.append("RECENT_HISTORY_UNSCOPED_LEGACY")
        test_turn_ids = {
            str(message.turn_id)
            for message in persisted_messages
            if bool(message.metadata.get("test_mode", False))
        }
        if test_turn_ids:
            reasons.append("TEST_TURNS_EXCLUDED_FROM_RECENT")

        recent: list[dict[str, Any]] = []
        for message in persisted_messages:
            # respond() persists the input before compiling context so observability
            # survives provider failure. Do not echo that same current turn back as
            # history: the provider receives context.user_text separately exactly once.
            if str(message.turn_id) == str(request.turn_id):
                continue
            # A technical turn is excluded as one causal unit. This removes both
            # the test input and Amora's generated segments from future history.
            if str(message.turn_id) in test_turn_ids:
                continue
            if message.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
                continue
            if remote and not message.remote_processing_allowed:
                continue
            text = message.spoken_text or message.normalized_text or message.raw_text
            if not text:
                continue
            recent.append(
                {
                    "turn_id": str(message.turn_id),
                    "speaker_id": message.speaker_id,
                    "addressee_id": message.addressee_id,
                    "text": text[:600],
                    "source": message.source.value,
                    "status": message.status.value,
                }
            )
        recent = recent[-12:]
        reasons.append("CURRENT_TURN_EXCLUDED_FROM_RECENT")

        relationships: list[dict[str, Any]] = []
        for relation in self.identities.relationships:
            if request.speaker_id not in {relation.source, relation.target} and "ENTITY_AMORA" not in {relation.source, relation.target}:
                continue
            relationships.append(
                {
                    "source": relation.source,
                    "relation_type": relation.relation_type,
                    "target": relation.target,
                    "confidence": relation.confidence,
                }
            )

        self_snapshot = self.self_model.snapshot()
        if remote:
            # Self Model currently has no per-entry privacy column. Until it does,
            # send stable behavioral layers only; never arbitrary future layers.
            allowed_layers = {"core_identity", "traits", "preferences", "relationships", "current_state"}
            self_snapshot = {key: value for key, value in self_snapshot.items() if key in allowed_layers}
            reasons.append("SELF_MODEL_REMOTE_LAYER_ALLOWLIST")

        context = CognitiveContext(
            turn_id=request.turn_id,
            created_at=self.clock.now(),
            speaker_id=request.speaker_id,
            target_id=request.target_id,
            user_text=request.text,
            acir=acir,
            self_model=self_snapshot,
            relationships=relationships,
            mood={
                "valence": round(mood.valence, 6),
                "arousal": round(mood.arousal, 6),
                "dominance": round(mood.dominance, 6),
            },
            directed_affect=affect,
            drives=drives,
            active_intention=intention,
            memories=memories,
            world_claims=claims,
            recent_messages=recent,
            privacy_scope=request.privacy_scope,
            remote_processing_allowed=remote,
            token_budget=token_budget,
            reason_codes=reasons,
        )
        dialogue_plan = self.dialogue.plan(context)
        context.dialogue_plan = dialogue_plan.model_dump(mode="json")
        if context.acir is not None:
            # Normal providers already consume the authoritative local dialogue
            # plan. Binding the input ACIR here makes canonical speaker/addressee
            # semantics part of that existing provider contract without creating a
            # second platform-specific prompt path.
            context.dialogue_plan["input_acir"] = context.acir
            context.reason_codes.append("ACIR_BOUND_TO_DIALOGUE_PLAN")
        context.reason_codes.extend(
            code for code in dialogue_plan.reason_codes if code not in context.reason_codes
        )
        context.reason_codes.append(f"DIALOGUE_ACT_{dialogue_plan.act.value.upper()}")
        return self._fit_budget(context)

    @staticmethod
    def _fit_budget(context: CognitiveContext) -> CognitiveContext:
        # Cheap deterministic token proxy (roughly 4 chars/token for Latin text).
        # Prefer dropping old conversation/world detail before relevant memories.
        def estimate() -> int:
            return max(1, len(context.model_dump_json()) // 4)

        while estimate() > context.token_budget and context.recent_messages:
            context.recent_messages.pop(0)
            if "CONTEXT_TRIMMED_RECENT" not in context.reason_codes:
                context.reason_codes.append("CONTEXT_TRIMMED_RECENT")
        while estimate() > context.token_budget and len(context.world_claims) > 2:
            context.world_claims.pop()
            if "CONTEXT_TRIMMED_WORLD" not in context.reason_codes:
                context.reason_codes.append("CONTEXT_TRIMMED_WORLD")
        while estimate() > context.token_budget and len(context.memories) > 2:
            context.memories.pop()
            if "CONTEXT_TRIMMED_MEMORY" not in context.reason_codes:
                context.reason_codes.append("CONTEXT_TRIMMED_MEMORY")
        return context

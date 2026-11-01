from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from amora import __version__
from amora.actions import ActionEngine, ActionStore
from amora.audio import EnergyVad, GroqWhisperSTT, SessionSpeakerTracker
from amora.autonomy import AutonomyEngine, AutonomyStore
from amora.avatar import AvatarEngine, AvatarStore
from amora.cognition import (
    CloudflareCognitiveProvider,
    CognitiveEngine,
    CognitiveStore,
    ContextCompiler,
    GeminiCognitiveProvider,
    GroqCognitiveProvider,
    OpenRouterCognitiveProvider,
)
from amora.conversation import ConversationEngine, ConversationStore
from amora.emotion import EmotionEngine, EmotionStore
from amora.events import AsyncEventBus
from amora.identity import (
    DeviceIdentity,
    DeviceTrust,
    EntityRegistry,
    PeopleProfileCatalogStore,
    PersonProfileService,
    SessionService,
    build_initial_registry,
)
from amora.language import ACIRCompiler, LanguageSocialPipeline, PerspectiveResolver
from amora.memory import MemoryService, MemoryStore
from amora.messaging import (
    MessagingStore,
    PlatformMessagingService,
    PlatformPresenceController,
    SocialAttentionRouter,
)
from amora.models import ModelRouter
from amora.observability import configure_logging
from amora.operations import OperationMonitor
from amora.pairing import PairingManager
from amora.secrets import SecretStore
from amora.settings import RuntimePaths, ServerSettings
from amora.social import AddresseeEngine, SocialSceneTracker
from amora.storage import AmoraDatabase
from amora.storage.backend import StorageBackend, BootstrapBackend, backend_from_environment
from amora.storage.migrations_v104 import LATEST_SCHEMA_VERSION
from amora.time import Clock, Scheduler, SystemClock
from amora.world import (
    PerspectiveConsolidator,
    RelationshipEvolutionService,
    SelfLayer,
    SelfModelService,
    WorldModelService,
    WorldStore,
)


_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(slots=True)
class AmoraCore:
    operations: OperationMonitor
    paths: RuntimePaths
    clock: Clock
    events: AsyncEventBus
    scheduler: Scheduler
    identities: EntityRegistry
    profiles: PersonProfileService
    sessions: SessionService
    secrets: SecretStore
    pairing: PairingManager
    database: AmoraDatabase
    memory: MemoryService
    world_store: WorldStore
    world: WorldModelService
    self_model: SelfModelService
    relationships: RelationshipEvolutionService
    perspective_consolidator: PerspectiveConsolidator
    emotion: EmotionEngine
    autonomy: AutonomyEngine
    cognition: CognitiveEngine
    conversation: ConversationEngine
    messaging_store: MessagingStore
    platform_presence: PlatformPresenceController
    messaging: PlatformMessagingService
    actions: ActionEngine
    avatar: AvatarEngine
    models: ModelRouter
    social: SocialSceneTracker
    addressee: AddresseeEngine
    perspective: PerspectiveResolver
    acir: ACIRCompiler
    language_social: LanguageSocialPipeline
    vad: EnergyVad
    stt: GroqWhisperSTT

    _closed: bool = field(default=False, init=False)

    @classmethod
    def bootstrap(cls, *, clock: Clock | None = None, storage: StorageBackend | None = None) -> "AmoraCore":
        tracked = BootstrapBackend(storage or backend_from_environment())
        try:
            core = cls._bootstrap(clock=clock, storage=tracked)
        except BaseException:
            tracked.finish(failed=True)
            raise
        tracked.finish()
        return core

    @classmethod
    def _bootstrap(cls, *, clock, storage):
        ServerSettings.from_environment().validate_persistence(remote=storage.capabilities.remote)
        paths = RuntimePaths.from_environment()
        paths.ensure()
        configure_logging(paths.logs)
        active_clock = clock or SystemClock()
        operations = OperationMonitor(active_clock)
        from amora.observability import remember_secret
        remember_secret(os.getenv("AMORA_VOICE_SERVICE_TOKEN"))
        remember_secret(os.getenv("AMORA_SECRET_LIBSQL_AUTH_TOKEN"))
        remember_secret(os.getenv("AMORA_MESSAGING_SERVICE_TOKEN"))
        secrets = SecretStore(paths.secrets)
        secrets.ensure_token("LOCAL_API_TOKEN")
        database = AmoraDatabase(paths.database, backend=storage)
        database.migrate()
        identities = database.load_registry()
        if identities is None:
            identities = build_initial_registry()
            database.save_registry(identities)

        people_catalog, people_fingerprint = PeopleProfileCatalogStore().load()
        profiles = PersonProfileService(
            store=database,
            identities=identities,
            clock=active_clock,
            persist_registry=database.save_registry,
        )
        profiles.apply_catalog(people_catalog, fingerprint=people_fingerprint)
        profiles.import_configured_private_bindings(
            default_path=paths.secrets / "identity_bindings.json"
        )

        sessions = SessionService(database, active_clock)
        if database.get_device("DEVICE_A15") is None:
            sessions.register_device(
                DeviceIdentity(
                    id="DEVICE_A15",
                    owner_person_id="PERSON_BK",
                    device_type="android_server",
                    trust_level=DeviceTrust.LOCAL_TRUSTED,
                    credential_ref="LOCAL_API_TOKEN",
                    capabilities={"core_client", "private_context"},
                    last_seen=active_clock.now(),
                )
            )
        events = AsyncEventBus(event_store=database)
        scheduler = Scheduler(active_clock)
        social = SocialSceneTracker()
        addressee = AddresseeEngine(identities)
        perspective = PerspectiveResolver(identities)
        memory = MemoryService(MemoryStore(paths.database, active_clock, identities, backend=storage), active_clock)
        world_store = WorldStore(paths.database, active_clock, backend=storage)
        world = WorldModelService(world_store, active_clock)
        self_model = SelfModelService(world_store, active_clock)
        self_model.ensure_core_identity()
        relationships = RelationshipEvolutionService(
            world_store,
            active_clock,
            identities,
            persist_registry=database.save_registry,
        )
        relationships.seed_registry_relationships()
        perspective_consolidator = PerspectiveConsolidator(world)
        emotion = EmotionEngine(
            EmotionStore(paths.database, active_clock, backend=storage),
            active_clock,
            identities,
            events,
        )
        autonomy = AutonomyEngine(
            AutonomyStore(paths.database, active_clock, backend=storage),
            active_clock,
            identities,
            emotion,
            world,
            events,
        )
        models = ModelRouter()
        cognitive_store = CognitiveStore(paths.database, active_clock, backend=storage)
        cognition = CognitiveEngine(
            cognitive_store,
            ContextCompiler(
                clock=active_clock,
                identities=identities,
                memory=memory,
                world=world,
                self_model=self_model,
                emotion=emotion,
                autonomy=autonomy,
                social=social,
                store=cognitive_store,
                family_authority_ids=profiles.family_authorities(),
            ),
            models,
            active_clock,
            events,
            operations=operations,
        )

        groq_cognition = GroqCognitiveProvider(secrets)
        if groq_cognition.descriptor.available:
            cognition.register_provider(groq_cognition)

        cloudflare_cognition = CloudflareCognitiveProvider(secrets)
        cloudflare_zero_cost_confirmed = _env_true("AMORA_CLOUDFLARE_ZERO_COST_CONFIRMED")
        if cloudflare_cognition.descriptor.available and cloudflare_zero_cost_confirmed:
            cognition.register_provider(cloudflare_cognition)

        # Gemini remains an independent fallback. Groq registration may already
        # auto-discover it for backwards compatibility, so avoid duplication.
        gemini_cognition = GeminiCognitiveProvider(secrets)
        if gemini_cognition.descriptor.available and cognition.router.descriptor("gemini") is None:
            cognition.register_provider(gemini_cognition)

        # OpenRouter is intentionally registered only when the selected route is
        # explicitly free (`openrouter/free` or a :free model).
        openrouter_cognition = OpenRouterCognitiveProvider(secrets)
        if openrouter_cognition.descriptor.available:
            cognition.register_provider(openrouter_cognition)

        events.subscribe_bootstrap("cognition.action_suggested", autonomy.handle_cognitive_suggestion)
        conversation = ConversationEngine(
            ConversationStore(paths.database, active_clock, backend=storage),
            active_clock,
            social,
            emotion,
            events,
            cognition=cognition,
        )
        messaging_store = MessagingStore(database, active_clock)
        platform_presence = PlatformPresenceController(messaging_store, active_clock)
        messaging = PlatformMessagingService(
            store=messaging_store,
            attention=SocialAttentionRouter(profiles=profiles, clock=active_clock, store=messaging_store),
            cognition=cognition,
            conversation=conversation,
            clock=active_clock,
        )
        avatar = AvatarEngine(
            AvatarStore(paths.database, paths.avatar_assets, active_clock, backend=storage),
            active_clock,
            emotion,
            conversation,
            social,
        )
        events.subscribe_bootstrap("action.semantic_request", conversation.handle_semantic_event)
        events.subscribe_bootstrap("action.semantic_request", avatar.handle_semantic_event)
        actions = ActionEngine(
            ActionStore(paths.database, active_clock, backend=storage),
            active_clock,
            autonomy,
            emotion,
            events,
            semantic_sink=conversation.handle_semantic_event,
        )
        actions.bus.operations = operations
        return cls(
            operations=operations,
            paths=paths,
            clock=active_clock,
            events=events,
            scheduler=scheduler,
            identities=identities,
            profiles=profiles,
            sessions=sessions,
            secrets=secrets,
            pairing=PairingManager(paths.cache / "pairing.json", active_clock),
            database=database,
            memory=memory,
            world_store=world_store,
            world=world,
            self_model=self_model,
            relationships=relationships,
            perspective_consolidator=perspective_consolidator,
            emotion=emotion,
            autonomy=autonomy,
            cognition=cognition,
            conversation=conversation,
            messaging_store=messaging_store,
            platform_presence=platform_presence,
            messaging=messaging,
            actions=actions,
            avatar=avatar,
            models=models,
            social=social,
            addressee=addressee,
            perspective=perspective,
            acir=ACIRCompiler(perspective),
            language_social=LanguageSocialPipeline(
                clock=active_clock,
                events=events,
                social=social,
                addressee=addressee,
            ),
            vad=EnergyVad(),
            stt=GroqWhisperSTT(secrets),
        )

    def create_speaker_tracker(self, session_id: str) -> SessionSpeakerTracker:
        return SessionSpeakerTracker(session_id)

    def health_report(self) -> dict[str, object]:
        bk = self.identities.resolve_alias("BK")
        integrity = self.database.integrity_check()
        core_identity = self.self_model.get(SelfLayer.CORE_IDENTITY, "entity_id")
        mood = self.emotion.current_mood()
        current_intention = self.autonomy.store.current_intention()
        autonomy_state = self.autonomy.state()
        drives = {item.drive.value: round(item.need, 6) for item in self.autonomy.current_drives()}
        conversation = self.conversation.state()
        cognition = self.cognition.state()
        avatar = self.avatar.health()
        cloudflare_configured = (
            self.secrets.get("CLOUDFLARE_API_TOKEN") is not None
            and self.secrets.get("CLOUDFLARE_ACCOUNT_ID") is not None
        )
        openrouter_provider = self.models.descriptor("openrouter")
        return {
            "status": "ok" if integrity == "ok" else "degraded",
            "version": __version__,
            "database_integrity": integrity,
            "schema_version": self.database.schema_version(),
            "event_count": self.database.event_count(),
            "memory_count": self.memory.store.count(),
            "memory_fts_enabled": self.memory.store.fts_enabled,
            "world_claim_count": self.world_store.claim_count(),
            "canonical_world_state_count": len(self.world.current_state()),
            "self_entry_count": len(self.world_store.list_self_entries()),
            "self_core_identity": core_identity.value if core_identity else None,
            "emotion_event_count": self.emotion.store.event_count(),
            "mood_valence": round(mood.valence, 6),
            "mood_arousal": round(mood.arousal, 6),
            "mood_dominance": round(mood.dominance, 6),
            "pending_social_bids": len(self.emotion.pending_bids()),
            "pending_cognitive_suggestions": autonomy_state["pending_cognitive_suggestions"],
            "autonomy_decision_count": self.autonomy.store.decision_count(),
            "active_intention_kind": current_intention.kind.value if current_intention else None,
            "drives": drives,
            "action_plan_count": self.actions.store.plan_count(),
            "action_run_count": self.actions.store.run_count(),
            "tool_execution_count": self.actions.store.execution_count(),
            "plugin_count": self.actions.registry.plugin_count,
            "skill_count": self.actions.skills.count,
            "turn_decision_count": conversation["turn_decision_count"],
            "speech_pending_count": conversation["speech_pending_count"],
            "cognitive_turn_count": cognition["turn_count"],
            "cognitive_pending_count": cognition["pending_count"],
            "cognitive_provider_count": cognition["provider_count"],
            "cognitive_fallback_count": cognition["fallback_count"],
            "cognitive_last_provider": cognition["last_provider"],
            "cognitive_last_fallback_used": cognition["last_fallback_used"],
            "cognitive_last_reason_codes": cognition["last_reason_codes"],
            "messaging_audit_count": self.messaging_store.count_audit(),
            "messaging_outbound_pending_count": self.messaging_store.count_pending_outbound(),
            "bk_entity_present": bk is not None,
            "cloudflare_cognition_configured": cloudflare_configured,
            "openrouter_model": openrouter_provider.model if openrouter_provider else None,
            "schema_latest": LATEST_SCHEMA_VERSION,
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.database.close()


def _env_true(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in _TRUE_VALUES

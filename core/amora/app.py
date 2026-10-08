from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from amora import __version__
from amora.actions import ActionEngine, ActionStore
from amora.audio import EnergyVad, GroqWhisperSTT, SessionSpeakerTracker
from amora.autonomy import AutonomyEngine, AutonomyStore
from amora.emotion import EmotionEngine, EmotionStore
from amora.events import AsyncEventBus
from amora.identity import DeviceIdentity, DeviceTrust, EntityRegistry, SessionService, build_initial_registry
from amora.language import ACIRCompiler, LanguageSocialPipeline, PerspectiveResolver
from amora.memory import MemoryService, MemoryStore
from amora.models import ModelRouter
from amora.observability import configure_logging
from amora.pairing import PairingManager
from amora.secrets import SecretStore
from amora.settings import RuntimePaths
from amora.social import AddresseeEngine, SocialSceneTracker
from amora.storage import AmoraDatabase
from amora.time import Clock, Scheduler, SystemClock
from amora.world import (
    PerspectiveConsolidator,
    RelationshipEvolutionService,
    SelfLayer,
    SelfModelService,
    WorldModelService,
    WorldStore,
)


@dataclass(slots=True)
class AmoraCore:
    paths: RuntimePaths
    clock: Clock
    events: AsyncEventBus
    scheduler: Scheduler
    identities: EntityRegistry
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
    actions: ActionEngine
    models: ModelRouter
    social: SocialSceneTracker
    addressee: AddresseeEngine
    perspective: PerspectiveResolver
    acir: ACIRCompiler
    language_social: LanguageSocialPipeline
    vad: EnergyVad
    stt: GroqWhisperSTT

    @classmethod
    def bootstrap(cls, *, clock: Clock | None = None) -> "AmoraCore":
        paths = RuntimePaths.from_environment()
        paths.ensure()
        configure_logging(paths.logs)
        active_clock = clock or SystemClock()
        secrets = SecretStore(paths.secrets)
        secrets.ensure_token("LOCAL_API_TOKEN")
        database = AmoraDatabase(paths.database)
        database.migrate()
        identities = database.load_registry()
        if identities is None:
            identities = build_initial_registry()
            database.save_registry(identities)
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
        memory = MemoryService(MemoryStore(paths.database, active_clock, identities), active_clock)
        world_store = WorldStore(paths.database, active_clock)
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
            EmotionStore(paths.database, active_clock),
            active_clock,
            identities,
            events,
        )
        autonomy = AutonomyEngine(
            AutonomyStore(paths.database, active_clock),
            active_clock,
            identities,
            emotion,
            world,
            events,
        )
        actions = ActionEngine(
            ActionStore(paths.database, active_clock),
            active_clock,
            autonomy,
            emotion,
            events,
        )
        return cls(
            paths=paths,
            clock=active_clock,
            events=events,
            scheduler=scheduler,
            identities=identities,
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
            actions=actions,
            models=ModelRouter(),
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
        drives = {item.drive.value: round(item.need, 6) for item in self.autonomy.current_drives()}
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
            "autonomy_decision_count": self.autonomy.store.decision_count(),
            "active_intention_kind": current_intention.kind.value if current_intention else None,
            "drives": drives,
            "action_plan_count": self.actions.store.plan_count(),
            "action_run_count": self.actions.store.run_count(),
            "tool_execution_count": self.actions.store.execution_count(),
            "plugin_count": self.actions.registry.plugin_count,
            "skill_count": self.actions.skills.count,
            "entity_count": len(self.identities.entities),
            "relationship_count": len(self.identities.relationships),
            "active_sessions": self.database.active_session_count(self.clock.now()),
            "primary_person": bk.id if bk else None,
            "model_providers_registered": self.models.provider_count,
            "social_participant_count": len(self.social.scene.participants),
            "social_active_addressee": self.social.scene.active_addressee,
            "groq_stt_configured": self.secrets.get("GROQ_API_KEY") is not None,
        }

    def create_backup(self, name: str | None = None) -> Path:
        stamp = self.clock.now().strftime("%Y%m%dT%H%M%SZ")
        filename = name or f"amora-{stamp}.db"
        if Path(filename).name != filename or not filename.endswith(".db"):
            raise ValueError("backup name must be a simple .db filename")
        return self.database.backup_to(self.paths.backups / filename)

    def close(self) -> None:
        self.actions.close()
        self.autonomy.close()
        self.emotion.close()
        self.world_store.close()
        self.memory.close()
        self.database.close()

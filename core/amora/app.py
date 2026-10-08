from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from amora import __version__
from amora.events import AsyncEventBus
from amora.identity import DeviceIdentity, DeviceTrust, EntityRegistry, SessionService, build_initial_registry
from amora.models import ModelRouter
from amora.observability import configure_logging
from amora.secrets import SecretStore
from amora.settings import RuntimePaths
from amora.storage import AmoraDatabase
from amora.time import Clock, Scheduler, SystemClock


@dataclass(slots=True)
class AmoraCore:
    paths: RuntimePaths
    clock: Clock
    events: AsyncEventBus
    scheduler: Scheduler
    identities: EntityRegistry
    sessions: SessionService
    secrets: SecretStore
    database: AmoraDatabase
    models: ModelRouter

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
        return cls(
            paths=paths,
            clock=active_clock,
            events=AsyncEventBus(event_store=database),
            scheduler=Scheduler(active_clock),
            identities=identities,
            sessions=sessions,
            secrets=secrets,
            database=database,
            models=ModelRouter(),
        )

    def health_report(self) -> dict[str, object]:
        bk = self.identities.resolve_alias("BK")
        integrity = self.database.integrity_check()
        return {
            "status": "ok" if integrity == "ok" else "degraded",
            "version": __version__,
            "database_integrity": integrity,
            "schema_version": self.database.schema_version(),
            "event_count": self.database.event_count(),
            "entity_count": len(self.identities.entities),
            "relationship_count": len(self.identities.relationships),
            "active_sessions": self.database.active_session_count(self.clock.now()),
            "primary_person": bk.id if bk else None,
            "model_providers_registered": self.models.provider_count,
        }

    def create_backup(self, name: str | None = None) -> Path:
        stamp = self.clock.now().strftime("%Y%m%dT%H%M%SZ")
        filename = name or f"amora-{stamp}.db"
        if Path(filename).name != filename or not filename.endswith(".db"):
            raise ValueError("backup name must be a simple .db filename")
        return self.database.backup_to(self.paths.backups / filename)

    def close(self) -> None:
        self.database.close()

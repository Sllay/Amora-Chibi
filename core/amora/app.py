from __future__ import annotations

from dataclasses import dataclass

from amora.events import AsyncEventBus
from amora.identity import EntityRegistry, build_initial_registry
from amora.models import ModelRouter
from amora.settings import RuntimePaths
from amora.storage import AmoraDatabase
from amora.time import Scheduler, SystemClock


@dataclass(slots=True)
class AmoraCore:
    paths: RuntimePaths
    events: AsyncEventBus
    scheduler: Scheduler
    identities: EntityRegistry
    database: AmoraDatabase
    models: ModelRouter

    @classmethod
    def bootstrap(cls) -> "AmoraCore":
        paths = RuntimePaths.from_environment()
        paths.ensure()
        database = AmoraDatabase(paths.database)
        database.migrate()
        return cls(paths, AsyncEventBus(), Scheduler(SystemClock()), build_initial_registry(), database, ModelRouter())

    def close(self) -> None:
        self.database.close()

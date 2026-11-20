"""PostgreSQL autonomy candidate: atomic multi-drive upsert without SQL rewriting."""
from __future__ import annotations

from pathlib import Path
from amora.storage.backend import StorageBackend
from amora.storage.postgres_runtime_connection import PostgresRuntimeError
from amora.storage.postgres_runtime_autonomy import DRIVE_BATCH
from amora.time import Clock

from .store import AutonomyStore
from .models import DriveState


class PostgresAutonomyStore(AutonomyStore):
    def __init__(self,path:Path,clock:Clock,*,backend:StorageBackend):
        if backend.capabilities.name!="postgres-candidate":
            raise PostgresRuntimeError("PG_AUTONOMY_BACKEND_REQUIRED")
        super().__init__(path,clock,backend=backend)

    def put_drives(self,states:list[DriveState]) -> None:
        if not states:
            return
        # The original one-statement SQLite VALUES list becomes a reviewed
        # psycopg batched UPSERT inside a single explicit PG transaction.
        with self._lock,self._conn:
            self._conn.executemany(DRIVE_BATCH,[
                (state.drive.value,state.need,state.baseline,
                 state.growth_per_hour,state.updated_at.isoformat())
                for state in states
            ])

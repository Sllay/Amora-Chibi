"""Dedicated cognition-only connection; no domain engines execute on its thread."""
from __future__ import annotations

import asyncio
from amora.storage.owned_lane import OwnedStorageLane


class CognitiveStorageLane(OwnedStorageLane):
    allowed = frozenset({"list_messages", "get_message_by_dedupe", "put_message",
                         "put_journal", "list_segments", "source_invalidated"})
    trace_name = "cognitive_db_lane"

    def __init__(self, store):
        super().__init__(thread_name="amora-cognitive-db")
        self.store = store
        self.compile_lock = asyncio.Lock()

    def _make_worker(self):
        return type(self.store)(self.store.database_path, self.store.clock,
                                backend=self.store.backend)

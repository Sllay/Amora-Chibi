"""Remote messaging persistence. No routing, identity or provider work in the lane."""
from __future__ import annotations

from amora.storage.database import AmoraDatabase
from amora.storage.owned_lane import OwnedStorageLane
from .generation_lifecycle import InboundGeneration, InboundGenerationLost
from .store import MessagingStore


class MessagingStorageWorker:
    def __init__(self, database, clock):
        self.database = AmoraDatabase(database.path, backend=database.backend)
        self.generations = InboundGeneration(self.database, clock)
        self.store = MessagingStore(self.database, clock)

    def close(self):
        self.database.close()

    def claim(self, *args):
        return self.generations.claim(*args)

    def release(self, *args, **kwargs):
        return self.generations.release(*args, **kwargs)

    def prepared(self, *args):
        return self.generations.prepared(*args)

    def get_audit(self, **kwargs):
        return self.store.get_audit(**kwargs)

    def append_audit(self, audit):
        return self.store.append_audit(audit)

    def persist_reply(self, message, decision, outbound, reasons, lease, native_hash):
        # Keep the existing durable prepared-response recovery boundary.
        # These are separate commits, not a new transaction or retry policy.
        if not self.generations.stage(*lease, outbound, decision, reasons):
            raise InboundGenerationLost("INBOUND_GENERATION_LEASE_LOST")
        return self.store.persist_prepared_reply(
            message, decision, outbound, reasons, lease=lease, native_hash=native_hash)


class MessagingStorageLane(OwnedStorageLane):
    trace_name = "messaging_db_lane"
    allowed = frozenset({"claim", "release", "prepared", "get_audit", "append_audit", "persist_reply"})

    def __init__(self, database, clock):
        super().__init__(thread_name="amora-messaging-db")
        self.database = database
        self.clock = clock

    def _make_worker(self):
        return MessagingStorageWorker(self.database, self.clock)

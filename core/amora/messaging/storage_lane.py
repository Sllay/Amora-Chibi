"""Remote messaging persistence. No routing, identity or provider work in the lane."""
from __future__ import annotations

from amora.storage.database import AmoraDatabase
from amora.storage.owned_lane import OwnedStorageLane
from .generation_lifecycle import InboundGeneration, InboundGenerationLost
from .store import MessagingStore
from .reply_jobs import ReplyJobRepository
from .visual_checkpoint import VisualCheckpointStore


class MessagingStorageWorker:
    def __init__(self, database, clock):
        self.database = AmoraDatabase(database.path, backend=database.backend)
        self.generations = InboundGeneration(self.database, clock)
        self.reply_jobs = None
        self.realtime_receipts = None
        self.visual_checkpoints = VisualCheckpointStore(self.database, clock)
        self.clock = clock
        if database.backend.capabilities.name == "postgres-candidate":
            from .postgres_store import PostgresMessagingStore
            self.store = PostgresMessagingStore(self.database, clock)
        else:
            self.store = MessagingStore(self.database, clock)

    def close(self):
        self.database.close()

    def claim(self, *args):
        return self.generations.claim(*args)

    def renew(self, *args, **kwargs):
        return self.generations.renew(*args, **kwargs)

    def release(self, *args, **kwargs):
        return self.generations.release(*args, **kwargs)

    def prepared(self, *args):
        return self.generations.prepared(*args)

    def get_audit(self, **kwargs):
        return self.store.get_audit(**kwargs)

    def append_audit(self, audit):
        return self.store.append_audit(audit)

    def visual_checkpoint(self, operation, *args):
        if operation not in {"load", "save", "source_current", "save_reference", "load_reference", "mark_reference_disputed"}:
            raise ValueError("VISUAL_CHECKPOINT_OPERATION_NOT_ALLOWED")
        return getattr(self.visual_checkpoints, operation)(*args)

    def realtime_receipt(self,operation,*args,**kwargs):
        from .realtime_receipts import RealtimeReceiptStore
        from amora.storage.realtime_receipt_migration import verify_sqlite_receipts
        from amora.latency_trace import trace_lock
        with trace_lock(self.database._lock):
            pg=self.database.backend.capabilities.name=="postgres-candidate"
            if operation=="verify":
                return (self.database._conn.verify_realtime_component() if pg
                        else verify_sqlite_receipts(self.database._conn))
            if operation not in {"read","prepare","confirm","reject_definitive","observe_create","orphan_candidates","orphan_read"}:
                raise ValueError("REALTIME_RECEIPT_OPERATION_NOT_ALLOWED")
            if self.realtime_receipts is None:
                self.realtime_receipts=RealtimeReceiptStore(self.database._conn,postgres=pg,reviewed=True)
            return getattr(self.realtime_receipts,operation)(*args,**kwargs)

    def reply_job(self, operation, *args, **kwargs):
        if self.reply_jobs is None:
            self.reply_jobs = ReplyJobRepository(self.database, self.clock)
        if operation == "finalize":
            claim, *rest = args
            return self.reply_jobs.finalize(claim, self.store, *rest)
        if operation == "stage":
            return self.generations.stage(*args)
        if operation == "prepared":
            return self.generations.prepared(*args)
        if operation not in {"state", "counts", "verify_schema", "admit", "enqueue_turn", "load", "claim",
                             "renew", "defer", "block", "cancel_invalidated",
                             "reconcile_missing", "reconcile_outbound", "causal_snapshot"}:
            raise ValueError("REPLY_JOB_OPERATION_NOT_ALLOWED")
        return getattr(self.reply_jobs, operation)(*args, **kwargs)

    def persist_reply(self, message, decision, outbound, reasons, lease, native_hash):
        # Keep the existing durable prepared-response recovery boundary.
        # These are separate commits, not a new transaction or retry policy.
        if not self.generations.stage(*lease, outbound, decision, reasons):
            raise InboundGenerationLost("INBOUND_GENERATION_LEASE_LOST")
        return self.store.persist_prepared_reply(
            message, decision, outbound, reasons, lease=lease, native_hash=native_hash)


class MessagingStorageLane(OwnedStorageLane):
    trace_name = "messaging_db_lane"
    allowed = frozenset({"claim", "renew", "release", "prepared", "get_audit", "append_audit", "persist_reply", "reply_job", "visual_checkpoint", "realtime_receipt"})

    def __init__(self, database, clock):
        super().__init__(thread_name="amora-messaging-db")
        self.database = database
        self.clock = clock

    def _make_worker(self):
        return MessagingStorageWorker(self.database, self.clock)

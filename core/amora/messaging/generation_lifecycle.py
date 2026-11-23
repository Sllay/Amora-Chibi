"""Durable generation ownership and prepared outbound recovery."""
from __future__ import annotations

from amora.latency_trace import trace_stage, trace_lock

import json
from datetime import timedelta

from amora.storage.backend import execute_atomic_statement
from amora.storage.postgres_runtime_generation import (
    CLAIM as PG_CLAIM, RELEASE as PG_RELEASE, STAGE as PG_STAGE,
    PREPARED as PG_PREPARED, INVALIDATE as PG_INVALIDATE, RENEW as PG_RENEW,
)
from .models import AttentionDecision, OutboundMessage


class InboundGenerationBusy(RuntimeError):
    pass


class InboundGenerationLost(RuntimeError):
    pass


class InboundGeneration:
    def __init__(self, database, clock):
        self.database = database
        self.clock = clock

    def _write(self, sql, args):
        db = self.database
        with trace_lock(db._lock):
            return execute_atomic_statement(db.backend, db._conn, sql, args).rowcount

    @trace_stage("generation_claim")
    def claim(self, turn_id, session_id, native_hash, revision, token, *, seconds=180, now=None):
        now = self.clock.now() if now is None else now
        if self.database.backend.capabilities.name == "postgres-candidate":
            return bool(self._write(PG_CLAIM, (
                str(turn_id),session_id,native_hash,revision,token,
                (now+timedelta(seconds=seconds)).isoformat(),
                now.isoformat(),
            )))
        return bool(self._write(
            """INSERT INTO messaging_inbound_generation(
                   turn_id,session_id,native_message_hash,transport_revision,lease_token,lease_until
               ) VALUES (?,?,?,?,?,?)
               ON CONFLICT(turn_id) DO UPDATE SET lease_token=excluded.lease_token,lease_until=excluded.lease_until
               WHERE messaging_inbound_generation.lease_token IS NULL
                  OR messaging_inbound_generation.lease_until<=?""",
            (str(turn_id), session_id, native_hash, revision, token,
             (now + timedelta(seconds=seconds)).isoformat(), now.isoformat()),
        ))

    @trace_stage("generation_renew")
    def renew(self, turn_id, token, *, seconds=180, now=None):
        """Extend only the current, unexpired and non-invalidated owner."""
        now = self.clock.now() if now is None else now
        args = ((now + timedelta(seconds=seconds)).isoformat(),
                str(turn_id), token, now.isoformat())
        if self.database.backend.capabilities.name == "postgres-candidate":
            return bool(self._write(PG_RENEW, args))
        return bool(self._write(
            """UPDATE messaging_inbound_generation SET lease_until=?
               WHERE turn_id=? AND lease_token=? AND lease_until>?
                 AND NOT EXISTS (SELECT 1 FROM memory_reflection_invalidations x
                     WHERE x.session_id=messaging_inbound_generation.session_id
                     AND x.native_message_hash=messaging_inbound_generation.native_message_hash
                     AND x.invalidated_through>=messaging_inbound_generation.transport_revision)""",
            args,
        ))

    @trace_stage("generation_release")
    def release(self, turn_id, token, *, completed=False):
        if self.database.backend.capabilities.name == "postgres-candidate":
            return bool(self._write(PG_RELEASE,(
                int(completed),int(completed),int(completed),
                str(turn_id),token,
            )))
        clear = ",prepared_outbound_json=NULL,prepared_decision_json=NULL,prepared_reasons_json=NULL" if completed else ""
        return bool(self._write(
            "UPDATE messaging_inbound_generation SET lease_token=NULL,lease_until=NULL"+clear+
            " WHERE turn_id=? AND lease_token=?",
            (str(turn_id), token),
        ))

    @trace_stage("generation_stage")
    def stage(self, turn_id, token, outbound, decision, reasons):
        if self.database.backend.capabilities.name == "postgres-candidate":
            return bool(self._write(PG_STAGE,(
                outbound.model_dump_json(),decision.model_dump_json(),
                json.dumps(reasons),str(turn_id),token,
                self.clock.now().isoformat(),
            )))
        return bool(self._write(
            """UPDATE messaging_inbound_generation SET prepared_outbound_json=?,prepared_decision_json=?,prepared_reasons_json=?
               WHERE turn_id=? AND lease_token=? AND lease_until>?
                 AND NOT EXISTS (SELECT 1 FROM memory_reflection_invalidations x
                     WHERE x.session_id=messaging_inbound_generation.session_id
                     AND x.native_message_hash=messaging_inbound_generation.native_message_hash
                     AND x.invalidated_through>=messaging_inbound_generation.transport_revision)""",
            (outbound.model_dump_json(), decision.model_dump_json(), json.dumps(reasons),
             str(turn_id), token, self.clock.now().isoformat()),
        ))

    @trace_stage("generation_prepared")
    def prepared(self, turn_id, token):
        with trace_lock(self.database._lock):
            if self.database.backend.capabilities.name == "postgres-candidate":
                query = PG_PREPARED
            else:
                query = (
                    "SELECT prepared_outbound_json,prepared_decision_json,prepared_reasons_json "
                    "FROM messaging_inbound_generation WHERE turn_id=? AND lease_token=?"
                )
            row = self.database._conn.execute(
                query,(str(turn_id),token),
            ).fetchone()
        if row is None or not row["prepared_outbound_json"]:
            return None
        return (OutboundMessage.model_validate_json(row["prepared_outbound_json"]),
                AttentionDecision.model_validate_json(row["prepared_decision_json"]),
                json.loads(row["prepared_reasons_json"]))

    def invalidate_prepared(self, session_id, native_hash, through_revision):
        if self.database.backend.capabilities.name == "postgres-candidate":
            return self._write(PG_INVALIDATE,(
                session_id,native_hash,through_revision,
            ))
        return self._write(
            "UPDATE messaging_inbound_generation SET prepared_outbound_json=NULL,"
            "prepared_decision_json=NULL,prepared_reasons_json=NULL "
            "WHERE session_id=? AND native_message_hash=? AND transport_revision<=?",
            (session_id, native_hash, through_revision),
        )

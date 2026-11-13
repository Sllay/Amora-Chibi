"""Durable generation ownership and prepared outbound recovery."""
from __future__ import annotations

import json
from datetime import timedelta

from amora.storage.backend import execute_atomic_statement
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
        with db._lock:
            return execute_atomic_statement(db.backend, db._conn, sql, args).rowcount

    def claim(self, turn_id, session_id, native_hash, revision, token, *, seconds=180):
        now = self.clock.now()
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

    def release(self, turn_id, token, *, completed=False):
        clear = ",prepared_outbound_json=NULL,prepared_decision_json=NULL,prepared_reasons_json=NULL" if completed else ""
        return bool(self._write(
            "UPDATE messaging_inbound_generation SET lease_token=NULL,lease_until=NULL"+clear+
            " WHERE turn_id=? AND lease_token=?",
            (str(turn_id), token),
        ))

    def stage(self, turn_id, token, outbound, decision, reasons):
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

    def prepared(self, turn_id, token):
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT prepared_outbound_json,prepared_decision_json,prepared_reasons_json "
                "FROM messaging_inbound_generation WHERE turn_id=? AND lease_token=?",
                (str(turn_id), token),
            ).fetchone()
        if row is None or not row["prepared_outbound_json"]:
            return None
        return (OutboundMessage.model_validate_json(row["prepared_outbound_json"]),
                AttentionDecision.model_validate_json(row["prepared_decision_json"]),
                json.loads(row["prepared_reasons_json"]))

    def invalidate_prepared(self, session_id, native_hash, through_revision):
        return self._write(
            "UPDATE messaging_inbound_generation SET prepared_outbound_json=NULL,"
            "prepared_decision_json=NULL,prepared_reasons_json=NULL "
            "WHERE session_id=? AND native_message_hash=? AND transport_revision<=?",
            (session_id, native_hash, through_revision),
        )

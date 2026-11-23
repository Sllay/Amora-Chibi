"""Reviewed PostgreSQL 18 ingress generation lease lifecycle.

All SQL is exact with stable fixed templates. No dynamic SQLite SQL release
fragments and no generic rewriting. Release clears prepared artifacts only on
a matching lease, and claim never takes over an unexpired lease.
"""
from __future__ import annotations

from .postgres_runtime_connection import ReviewedStatement

CLAIM="PG_GENERATION_CLAIM"
RELEASE="PG_GENERATION_RELEASE"
RENEW="PG_GENERATION_RENEW"
STAGE="PG_GENERATION_STAGE"
PREPARED="PG_GENERATION_PREPARED"
INVALIDATE="PG_GENERATION_INVALIDATE"


def generation_statements()->dict[str,ReviewedStatement]:
    return {
        CLAIM: ReviewedStatement("""
            INSERT INTO amora_pg_v18_lab.messaging_inbound_generation(
                turn_id,session_id,native_message_hash,transport_revision,
                lease_token,lease_until
            ) VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT(turn_id) DO UPDATE SET
                lease_token=EXCLUDED.lease_token,
                lease_until=EXCLUDED.lease_until
            WHERE messaging_inbound_generation.lease_token IS NULL
              OR messaging_inbound_generation.lease_until<=%s
        """,7),
        RENEW: ReviewedStatement("""
            UPDATE amora_pg_v18_lab.messaging_inbound_generation g
            SET lease_until=%s
            WHERE g.turn_id=%s AND g.lease_token=%s AND g.lease_until>%s
              AND NOT EXISTS (
                  SELECT 1 FROM amora_pg_v18_lab.memory_reflection_invalidations x
                  WHERE x.session_id=g.session_id
                    AND x.native_message_hash=g.native_message_hash
                    AND x.invalidated_through>=g.transport_revision
              )
        """,4),
        RELEASE: ReviewedStatement("""
            UPDATE amora_pg_v18_lab.messaging_inbound_generation
            SET lease_token=NULL,lease_until=NULL,
                prepared_outbound_json=CASE WHEN %s=1 THEN NULL
                    ELSE prepared_outbound_json END,
                prepared_decision_json=CASE WHEN %s=1 THEN NULL
                    ELSE prepared_decision_json END,
                prepared_reasons_json=CASE WHEN %s=1 THEN NULL
                    ELSE prepared_reasons_json END
            WHERE turn_id=%s AND lease_token=%s
        """,5),
        STAGE: ReviewedStatement("""
            UPDATE amora_pg_v18_lab.messaging_inbound_generation g
            SET prepared_outbound_json=%s,prepared_decision_json=%s,
                prepared_reasons_json=%s
            WHERE g.turn_id=%s AND g.lease_token=%s AND g.lease_until>%s
              AND NOT EXISTS(
                  SELECT 1 FROM amora_pg_v18_lab.memory_reflection_invalidations x
                  WHERE x.session_id=g.session_id
                    AND x.native_message_hash=g.native_message_hash
                    AND x.invalidated_through>=g.transport_revision
              )
        """,6),
        PREPARED: ReviewedStatement("""
            SELECT prepared_outbound_json,prepared_decision_json,
                   prepared_reasons_json
            FROM amora_pg_v18_lab.messaging_inbound_generation
            WHERE turn_id=%s AND lease_token=%s
        """,2,1),
        INVALIDATE: ReviewedStatement("""
            UPDATE amora_pg_v18_lab.messaging_inbound_generation
            SET prepared_outbound_json=NULL,prepared_decision_json=NULL,
                prepared_reasons_json=NULL
            WHERE session_id=%s AND native_message_hash=%s
              AND transport_revision<=%s
        """,3),
    }

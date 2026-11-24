"""Reviewed additive PG18 reply-job component migration, no implicit activation.

The caller must stop ingress and Core writers, independently verify backup
and restore, and supply an already pinned PG18 target. This module never
loads credentials, selects a destination, deletes rows or downgrades v18.
"""
from .postgres_runtime_connection import PostgresTargetPolicy

REPLY_SCHEMA_VERSION = 1
REPLY_SCHEMA_COMMENT = "amora:durable-reply:v1"
MIGRATION_APPROVAL = "AUTHORIZE_VERIFIED_REPLY_SCHEMA_V1"
PG_REPLY_DDL = (
    """
            CREATE TABLE amora_pg_v18_lab.messaging_reply_jobs (
                job_seq BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                turn_id TEXT NOT NULL UNIQUE,
                input_id TEXT NOT NULL UNIQUE REFERENCES
                    amora_pg_v18_lab.conversation_messages(id) ON DELETE CASCADE,
                session_id TEXT NOT NULL,
                native_message_hash TEXT NOT NULL,
                transport_revision BIGINT NOT NULL CHECK(transport_revision>=1),
                status TEXT NOT NULL CHECK(status IN
                    ('pending','running','retry_wait','ready','sent','blocked','cancelled')),
                attempts BIGINT NOT NULL DEFAULT 0 CHECK(attempts>=0),
                created_at TEXT NOT NULL,
                available_at TEXT NOT NULL,
                lease_token TEXT,
                lease_until TEXT,
                last_reason TEXT,
                outbound_id TEXT,
                CHECK ((status='running' AND lease_token IS NOT NULL AND lease_until IS NOT NULL)
                    OR (status<>'running' AND lease_token IS NULL AND lease_until IS NULL)),
                CHECK (status NOT IN ('ready','sent') OR outbound_id IS NOT NULL)
            )
        """,
    """
            CREATE INDEX idx_messaging_reply_due
            ON amora_pg_v18_lab.messaging_reply_jobs(status,available_at,lease_until)
        """,
    """
            CREATE INDEX idx_messaging_reply_session
            ON amora_pg_v18_lab.messaging_reply_jobs(session_id,created_at,job_seq,status)
        """,
    """
            CREATE INDEX idx_messaging_reply_source
            ON amora_pg_v18_lab.messaging_reply_jobs(session_id,native_message_hash,transport_revision)
        """,
    "COMMENT ON TABLE amora_pg_v18_lab.messaging_reply_jobs IS 'amora:durable-reply:v1'",
)

_COLUMNS = (
    ("job_seq", "bigint", "NO", "YES"),
    ("turn_id", "text", "NO", "NO"),
    ("input_id", "text", "NO", "NO"),
    ("session_id", "text", "NO", "NO"),
    ("native_message_hash", "text", "NO", "NO"),
    ("transport_revision", "bigint", "NO", "NO"),
    ("status", "text", "NO", "NO"),
    ("attempts", "bigint", "NO", "NO"),
    ("created_at", "text", "NO", "NO"),
    ("available_at", "text", "NO", "NO"),
    ("lease_token", "text", "YES", "NO"),
    ("lease_until", "text", "YES", "NO"),
    ("last_reason", "text", "YES", "NO"),
    ("outbound_id", "text", "YES", "NO"),
)


def verify_target(raw, target):
    if not isinstance(target, PostgresTargetPolicy) or target.schema != "amora_pg_v18_lab":
        raise ValueError("REPLY_SCHEMA_TARGET_INVALID")
    target.verify(raw)
    if getattr(raw, "closed", True) or getattr(raw, "autocommit", None) is not True:
        raise ValueError("REPLY_SCHEMA_AUTOCOMMIT_REQUIRED")


def review_reply_schema(raw, target):
    """Read-only structural review; never adopts an unknown existing table."""
    verify_target(raw, target)
    with raw.transaction():
        if raw.execute("SELECT MAX(version) FROM amora_pg_v18_lab.schema_migrations").fetchone()[0] != 18:
            raise ValueError("REPLY_SCHEMA_BASE_VERSION_REQUIRED")
        present = raw.execute("SELECT to_regclass('amora_pg_v18_lab.messaging_reply_jobs') IS NOT NULL").fetchone()[0]
        if not present:
            return {"state": "absent", "version": 0, "base_version": 18}
        comment = raw.execute("""SELECT obj_description(
            'amora_pg_v18_lab.messaging_reply_jobs'::regclass, 'pg_class')""").fetchone()[0]
        columns = tuple(raw.execute("""SELECT column_name,data_type,is_nullable,is_identity
            FROM information_schema.columns
            WHERE table_schema='amora_pg_v18_lab' AND table_name='messaging_reply_jobs'
            ORDER BY ordinal_position""").fetchall())
        indexes = dict(raw.execute("""SELECT indexname,indexdef FROM pg_indexes
            WHERE schemaname='amora_pg_v18_lab' AND tablename='messaging_reply_jobs'""").fetchall())
        constraints = raw.execute("""SELECT contype,pg_get_constraintdef(oid)
            FROM pg_constraint WHERE conrelid='amora_pg_v18_lab.messaging_reply_jobs'::regclass""").fetchall()
        expected_indexes = {
            "idx_messaging_reply_due": "(status, available_at, lease_until)",
            "idx_messaging_reply_session": "(session_id, created_at, job_seq, status)",
            "idx_messaging_reply_source": "(session_id, native_message_hash, transport_revision)",
        }
        keys = {definition for kind, definition in constraints if kind in {"p", "u"}}
        fk = [definition for kind, definition in constraints if kind == "f"]
        checks = [definition for kind, definition in constraints if kind == "c"]
        if (comment != REPLY_SCHEMA_COMMENT or columns != _COLUMNS
                or any(not indexes.get(name, "").endswith(suffix)
                       for name, suffix in expected_indexes.items())
                or keys != {"PRIMARY KEY (job_seq)", "UNIQUE (turn_id)", "UNIQUE (input_id)"}
                or len(fk) != 1 or "FOREIGN KEY (input_id)" not in fk[0]
                or "conversation_messages(id) ON DELETE CASCADE" not in fk[0]
                or len(checks) != 5):
            raise ValueError("REPLY_SCHEMA_STRUCTURAL_MISMATCH")
        return {"state": "prepared", "version": REPLY_SCHEMA_VERSION, "base_version": 18}


def prepare_reply_schema(raw, target, *, approval):
    """One explicit additive transaction, idempotent only for reviewed v1.

    Commit-unknown callers must use review_reply_schema before any retry.
    The operator is responsible for the independent backup/restore receipt
    and exclusive maintenance window; the advisory lock serializes installers.
    """
    verify_target(raw, target)
    if approval != MIGRATION_APPROVAL:
        raise ValueError("REPLY_SCHEMA_OPERATOR_APPROVAL_REQUIRED")
    with raw.transaction():
        raw.execute("SELECT pg_advisory_xact_lock(8147108342241061)")
        state = review_reply_schema(raw, target)
        if state["state"] == "prepared":
            return state
        for sql in PG_REPLY_DDL:
            raw.execute(sql)
        state = review_reply_schema(raw, target)
        if state["state"] != "prepared":
            raise ValueError("REPLY_SCHEMA_NOT_PREPARED")
    return state

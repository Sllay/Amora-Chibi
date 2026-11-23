"""RUN 01 candidate DDL; never auto-applied to a serving Core.

The queue references the existing canonical conversation input. This is an
additive candidate for isolated migration/recovery tests only. Runtime schema
18 and the existing schema 19 opt-in remain unchanged.
"""

REPLY_JOB_CANDIDATE_SQL = """
CREATE TABLE IF NOT EXISTS messaging_reply_jobs (
    turn_id TEXT PRIMARY KEY,
    input_id TEXT NOT NULL UNIQUE REFERENCES conversation_messages(id) ON DELETE RESTRICT,
    session_id TEXT NOT NULL,
    native_message_hash TEXT NOT NULL,
    transport_revision INTEGER NOT NULL CHECK(transport_revision >= 1),
    status TEXT NOT NULL CHECK(status IN
        ('pending','running','retry_wait','ready','sent','blocked','cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    created_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    lease_token TEXT,
    lease_until TEXT,
    last_reason TEXT,
    outbound_id TEXT,
    CHECK ((status='running' AND lease_token IS NOT NULL AND lease_until IS NOT NULL)
        OR (status<>'running' AND lease_token IS NULL AND lease_until IS NULL)),
    CHECK (status NOT IN ('ready','sent') OR outbound_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_messaging_reply_due
    ON messaging_reply_jobs(status,available_at,lease_until);
CREATE INDEX IF NOT EXISTS idx_messaging_reply_session
    ON messaging_reply_jobs(session_id,created_at,turn_id,status);
CREATE INDEX IF NOT EXISTS idx_messaging_reply_source
    ON messaging_reply_jobs(session_id,native_message_hash,transport_revision);
"""

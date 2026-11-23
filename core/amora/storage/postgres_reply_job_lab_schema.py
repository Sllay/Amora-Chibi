"""Install reply-job candidate in the disposable PostgreSQL 18 lab only."""
from .postgres_memory_lab import ensure_local_lab


def install_reply_job_lab(raw):
    ensure_local_lab(raw)
    with raw.transaction():
        raw.execute("""
            CREATE TABLE amora_pg_v18_lab.messaging_reply_jobs (
                turn_id TEXT PRIMARY KEY,
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
        """)
        raw.execute("""
            CREATE INDEX idx_messaging_reply_due
            ON amora_pg_v18_lab.messaging_reply_jobs(status,available_at,lease_until)
        """)
        raw.execute("""
            CREATE INDEX idx_messaging_reply_session
            ON amora_pg_v18_lab.messaging_reply_jobs(session_id,created_at,turn_id,status)
        """)
        raw.execute("""
            CREATE INDEX idx_messaging_reply_source
            ON amora_pg_v18_lab.messaging_reply_jobs(session_id,native_message_hash,transport_revision)
        """)

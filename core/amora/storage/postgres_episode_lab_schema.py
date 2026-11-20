"""Explicit disposable PG18 D.3C.2A episode queue schema (not runtime v19).

Accepts caller-owned PG18 localhost connection only. Creates additive candidate
tables on an already prepared synthetic schema after the v18 import. Does not
change schema_migrations, alter real Aiven, or enable the episode worker.
"""
from __future__ import annotations
from .postgres_memory_lab import ensure_local_lab

def install_episode_queue_lab(raw)->None:
    ensure_local_lab(raw)
    with raw.transaction():
        raw.execute("""
            CREATE TABLE amora_pg_v18_lab.sensory_episode_jobs (
                job_id TEXT PRIMARY KEY,
                selection_json TEXT NOT NULL
                    CHECK(length(selection_json)<=4096),
                session_id TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN (
                    'pending','claimed','done','failed','cancelled'
                )),
                attempts BIGINT NOT NULL DEFAULT 0 CHECK(attempts>=0),
                created_at TEXT NOT NULL,
                available_at TEXT NOT NULL,
                lease_token TEXT,
                lease_until TEXT,
                last_reason TEXT,
                CHECK (
                    (status='claimed' AND lease_token IS NOT NULL
                                      AND lease_until IS NOT NULL)
                    OR
                    (status<>'claimed' AND lease_token IS NULL
                                       AND lease_until IS NULL)
                )
            )
        """)
        raw.execute("""
            CREATE INDEX idx_sensory_episode_due
            ON amora_pg_v18_lab.sensory_episode_jobs(
                status,available_at,lease_until
            )
        """)
        raw.execute("""
            CREATE INDEX idx_sensory_episode_session_status
            ON amora_pg_v18_lab.sensory_episode_jobs(correlation_id,status)
        """)
        raw.execute("""
            CREATE TABLE amora_pg_v18_lab.sensory_episode_job_evidence (
                job_id TEXT NOT NULL REFERENCES
                    amora_pg_v18_lab.sensory_episode_jobs(job_id)
                    ON DELETE CASCADE,
                event_id TEXT NOT NULL REFERENCES
                    amora_pg_v18_lab.events(id) ON DELETE RESTRICT,
                PRIMARY KEY(job_id,event_id)
            )
        """)
        raw.execute("""
            CREATE INDEX idx_sensory_episode_evidence
            ON amora_pg_v18_lab.sensory_episode_job_evidence(event_id,job_id)
        """)
        raw.execute("""
            CREATE TABLE amora_pg_v18_lab.sensory_episode_invalidations (
                event_id TEXT PRIMARY KEY,
                invalidated_at TEXT NOT NULL
            )
        """)

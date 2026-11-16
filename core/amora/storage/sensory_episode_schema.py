"""Candidate D.3C.2A DDL; NOT registered as a runtime migration.

Apply explicitly only in isolated engineering tests until C.2B/C is certified.
The canonical schema remains version 18.
"""

SENSORY_EPISODE_QUEUE_CANDIDATE_SQL = """
CREATE TABLE IF NOT EXISTS sensory_episode_jobs (
    job_id TEXT PRIMARY KEY,
    selection_json TEXT NOT NULL CHECK(length(selection_json) <= 4096),
    session_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','claimed','done','failed','cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    created_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    lease_token TEXT,
    lease_until TEXT,
    last_reason TEXT,
    CHECK ((status='claimed' AND lease_token IS NOT NULL AND lease_until IS NOT NULL)
           OR (status<>'claimed' AND lease_token IS NULL AND lease_until IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_sensory_episode_due
    ON sensory_episode_jobs(status, available_at, lease_until);
CREATE INDEX IF NOT EXISTS idx_sensory_episode_session_status
    ON sensory_episode_jobs(correlation_id, status);
CREATE TABLE IF NOT EXISTS sensory_episode_job_evidence (
    job_id TEXT NOT NULL REFERENCES sensory_episode_jobs(job_id) ON DELETE CASCADE,
    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
    PRIMARY KEY(job_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_sensory_episode_evidence
    ON sensory_episode_job_evidence(event_id, job_id);
CREATE TABLE IF NOT EXISTS sensory_episode_invalidations (
    event_id TEXT PRIMARY KEY,
    invalidated_at TEXT NOT NULL
);
"""


CANDIDATE_TABLES = frozenset({
    "sensory_episode_jobs", "sensory_episode_job_evidence", "sensory_episode_invalidations",
})
CANDIDATE_INDEXES = frozenset({
    "idx_sensory_episode_due", "idx_sensory_episode_session_status", "idx_sensory_episode_evidence",
})


def verify_candidate_ddl(connection):
    """Validate reviewed table/index definitions on a SQLite snapshot."""
    import sqlite3
    from contextlib import closing

    names = CANDIDATE_TABLES | CANDIDATE_INDEXES
    with closing(sqlite3.connect(":memory:")) as model:
        model.execute("CREATE TABLE events (id TEXT PRIMARY KEY)")
        model.executescript(SENSORY_EPISODE_QUEUE_CANDIDATE_SQL)
        expected = dict(model.execute("SELECT name,sql FROM sqlite_schema WHERE sql IS NOT NULL"))
    # RemoteConnection rows iterate column NAMES, not positional values.
    # Numeric indexing is supported by both sqlite3.Row and Hrana Row.
    actual = {row[0]: row[1] for row in connection.execute(
        "SELECT name,sql FROM sqlite_schema WHERE sql IS NOT NULL"
    )}
    if any(actual.get(name) != expected[name] for name in names):
        raise ValueError("SCHEMA19_DDL_MISMATCH")

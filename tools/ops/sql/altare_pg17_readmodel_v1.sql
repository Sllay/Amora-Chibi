-- Proposed Altare PostgreSQL 17 read-model schema v1.
-- NOT executed by the Core or existing migrations. Provision only in
-- isolated Altare staging after operator approval and a PG17 compatibility test.
-- It is not a dump or copy of Amora's main PG18 schema v18.
BEGIN;
CREATE SCHEMA IF NOT EXISTS amora_readmodel_v1;

CREATE TABLE IF NOT EXISTS amora_readmodel_v1.memory_excerpts (
  memory_id TEXT NOT NULL,
  owner_entity_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  summary TEXT NOT NULL,
  privacy_scope TEXT NOT NULL CHECK (privacy_scope = 'PERSONAL'),
  origin_updated_at TIMESTAMPTZ NOT NULL,
  origin_sequence BIGINT NOT NULL CHECK (origin_sequence > 0),
  materialized_at TIMESTAMPTZ NOT NULL,
  checksum CHAR(64) NOT NULL,
  valid_until TIMESTAMPTZ,
  archived BOOLEAN NOT NULL DEFAULT FALSE,
  projection_schema INTEGER NOT NULL DEFAULT 1 CHECK (projection_schema = 1),
  PRIMARY KEY (owner_entity_id, memory_id)
);

CREATE INDEX IF NOT EXISTS idx_amora_readmodel_owner_recent
  ON amora_readmodel_v1.memory_excerpts
  (owner_entity_id, materialized_at DESC)
  WHERE archived = FALSE;

-- Cursor must be advanced in the SAME transaction as all successfully
-- applied changes from its batch. Never acknowledge a failed batch.
CREATE TABLE IF NOT EXISTS amora_readmodel_v1.projection_cursor (
  stream_name TEXT PRIMARY KEY,
  last_committed_sequence BIGINT NOT NULL CHECK (last_committed_sequence >= 0),
  applied_at TIMESTAMPTZ NOT NULL
);
COMMIT;

-- Outbox application template (NOT executable deployment code):
-- BEGIN;
-- INSERT INTO memory_excerpts (...) VALUES (...)
-- ON CONFLICT (owner_entity_id, memory_id) DO UPDATE SET ...
-- WHERE memory_excerpts.origin_sequence <= EXCLUDED.origin_sequence;
-- INSERT INTO projection_cursor (...) VALUES (...)
-- ON CONFLICT(stream_name) DO UPDATE SET
-- last_committed_sequence=GREATEST(...), applied_at=EXCLUDED.applied_at;
-- COMMIT;
--
-- Tombstones, privacy changes, outbox epochs and re-reads need separate
-- audited, transactional logic. Never use this read-model as a source of truth.

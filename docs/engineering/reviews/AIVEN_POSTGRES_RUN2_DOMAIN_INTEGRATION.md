# Aiven PostgreSQL — Run 2: integration of original Core domains

Branch: `feat/aiven-full-runtime-integration`.
Status: **domain implementation in progress; NOT full-Core PostgreSQL certification**.
Run 2 is for coding/integration only. Run 3 is reserved for remaining SQL dependencies and diagnosis. Fixes, CI certification and functional validation happen afterwards. Nothing here authorizes production cutover.

## Implemented in this branch

The isolated PostgreSQL 18 Core candidate assembles manually reviewed, schema-qualified SQL for these domain families:

| Domain | Code entrypoint | Notes |
| --- | --- | --- |
| Core events, identity, profiles, sessions | `postgres_runtime_identity.py` | Canonical `AmoraDatabase` SQL, stable identity mappings and event dedupe |
| Conversation and voice | `postgres_runtime_conversation.py`, `conversation/postgres_store.py` | Original `ConversationStore` methods; row-level claim locking; durable per-turn stop with explicit auxiliary table |
| Long-term memory | `postgres_runtime_memory.py`, `memory/postgres_store.py` | Atomic record/entities/tags/search document/postings writes, ranked candidate pool, RRF/MMR, entity ranks and owner privacy |
| Cognition/history | `postgres_runtime_cognition.py`, `cognition/postgres_store.py` | Original journal/segment SQL and bounded, scoped, JSONB-aware history read |
| World/self/relationships | `postgres_runtime_world.py`, `world/postgres_store.py` | Bound dynamic selections, world evidence batching, belief and relationship state |
| Actions | `postgres_runtime_actions.py` | Original action plans, executions and terminal run records |
| Avatar | `postgres_runtime_avatar.py` | Avatar metadata plus SQLBlobStore byte array operations |
| Autonomy | `postgres_runtime_autonomy.py`, `autonomy/postgres_store.py` | Drive and intention writes, atomic drive snapshot batch, decision deduplication |
| Emotion/social bids | `postgres_runtime_emotion.py`, `emotion/postgres_store.py` | Mood, affect, emotional events and expiring bids |
| Messaging | `postgres_runtime_messaging.py`, `messaging/postgres_store.py` | Audit, presence, outbound queue, generation lease and single-use memory disclosure fencing |

Each mapping is explicit: there is **no generic SQLite-to-PostgreSQL SQL translator**. `compiled_domain_registry()` rejects duplicate source SQL mappings, and an unmapped statement fails closed.

`app.py` selects specialized memory, world, emotion, autonomy, cognition, conversation and messaging stores **only when a PostgreSQL candidate is explicitly injected**. The regular SQLite/libSQL runtime path remains unchanged.

The disposable PostgreSQL setup is described in `postgres_runtime_synthetic_setup.py`: canonical *invented* SQLite v18 fixture → empty PG18 lab schema → typed row import → posting index and speech stop schema. It is not a production importer and cannot accept Aiven or private PCS.4C snapshots.

## Non-negotiable missing gates / known incomplete paths

1. Several additional serving Core helpers outside the main stores still use SQLite-specific dialect and dynamic SQL: incoming message generation lifecycle, reflection invalidation, agenda and temporal initiatives, sensory episode v19 candidate, projection/replica coherence, and ancillary modules. **Do not claim whole-Core bootstrap or end-to-end Discord conversation is operational** while these paths remain.
2. PostgreSQL-specific memory `episode_lease` writes and `record_verified_access` replica revision path deliberately fail closed. A correct v19 schema migration and externally maintained authoritative revision are required.
3. The durable `speech_turn_stops` table and FTS/BM25 posting tables are auxiliary experimental structures beyond the canonical SQLite 51-object inventory. Their production DDL, retention and recovery policies have NOT been approved.
4. PG FTS ranking, tokenizer, privacy gates, leases and text history must undergo semantic comparison with real disposable PostgreSQL 18 before certification. The schema and in-memory tokenizer code alone do not establish FTS5 equivalence.
5. PostgreSQL-native backup/restore, capacity, cutover, rollback, PG session isolation and complete Python 3.11–3.14 Core tests remain separate implementation and validation gates.
6. Some original store APIs intentionally expose unscoped internal queries. An internal query registry does not grant principal authorization; users must pass through original Core identity/policy gates.
7. The branch has **not** deployed the PostgreSQL candidate, populated Aiven, read a private snapshot, altered Turso, switched `AMORA_STORAGE_BACKEND`, or certified green CI.

## Next development stage

Run 3 should inventory all still-unreviewed SQL at serving call sites, finish essential ancillary glue and produce a cross-domain diagnostic ledger. After Run 3, test/repair/retest the entire synthetic PG18 Core lifecycle and only then decide whether the production migration can be scheduled.

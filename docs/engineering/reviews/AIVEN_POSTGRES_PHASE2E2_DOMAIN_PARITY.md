# Aiven Phase 2E.2 — identity and conversation SQL parity on PostgreSQL 18

Status: **synthetic, isolated PostgreSQL CI laboratory**, not a production Core adapter.

## Source contracts inspected

These SQL families were read directly from `core/amora/storage/database.py`, `core/amora/conversation/store.py`, and the canonical v18 migrations:
- `entities`, `aliases`, `relationships`: entity upsert preserves `payload_json`, old aliases are replaced inside the same transaction, `normalized_alias` is a unique primary key, relationships have composite `UNIQUE(source, relation_type, target)` and real foreign keys.
- `conversation_turn_decisions`: unique ID and dedupe key, INSERT return success or duplicate, stored JSON string and most-recent-first capped listings.
- `conversation_messages`: session, speaker, turn, dedupe, privacy scope, remote processing flag and JSON metadata fields. This checkpoint deliberately uses **explicit synthetic message insert/read predicates**; the wider canonical conversation runtime, Discord permission pipeline and voice queue have NOT been migrated.

## Code and real CI tests

`core/amora/storage/postgres_domain_parity_lab.py` implements reviewed PostgreSQL SQL for:
- Atomic identity and alias updates and relationship upserts, with fail-closed uniqueness and FK errors.
- Insert-only turn decisions with duplicate detection, retrieving stored JSON by dedupe key and bounded result listings.
- Insert-only invented messages, with idempotent dedupe and exact speaker+session scope on reads. The read predicate excludes AUTH/SECRET and optionally requires `remote_processing_allowed = 1`. This is a narrow **test projection**, not complete Core identity authorization.
- All functions call `ensure_local_lab` before any SQL, accepting only loopback PostgreSQL 18 named `amora_pg_synthetic`. No DSN, secrets, filesystem snapshot paths or backend selector.

`tests/test_postgres_domain_parity.py` runs canonical SQLite v18 **in memory** alongside disposable PostgreSQL 18:
- Checks complete deterministic registry projections across names, aliases, relationships, duplicate relationships and updates.
- Forces duplicate alias and missing-FK failures, comparing rollback and preserved data on both databases.
- Compares dedupe behavior, stored JSON and bounded chronological turn results.
- Compares invented messages across speakers, sessions, PUBLIC/PERSONAL/AUTH/SECRET, remote-only filtering and dedupe collisions.
- Explicitly refuses Aiven/remote connections and invalid JSON/scope without writing.

## Boundaries for future stages

1. These isolated functions are not wired to `AmoraDatabase`, `ConversationStore`, `backend_from_environment`, Discord, Northflank or Aiven. A wider serving adapter cannot rely on a partial allowlist.
2. `SpeechUtterance` leases are not covered here. Queue claiming, expiry, cancellation, voice barge-in, worker concurrency and crash recovery need separate **claim/ack/cancel** tests before release.
3. Real identity-binder/trust/session auth, reflection invalidation and disclosure rules are more extensive than this limited scoped-message query. They require full authorization/integration parity.
4. This does not read PCS.4C, import any actual memory or change the Turso/SQLite runtime. The historical 48,059 records are not asserted to be fresh.
5. The stacked PR chain remains isolated from `main`. A production cutover requires full Core SQL dialect and leases, complete message flows, 51-table data parity, restart and rollback, resource budget, source freshness reconciliation and operator approval.

No Northflank/Aiven action is required from the operator.

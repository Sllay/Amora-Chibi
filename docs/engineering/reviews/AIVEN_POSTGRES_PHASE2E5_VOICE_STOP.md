# Aiven Phase 2E.5 — durable, race-safe voice stop tombstones

Status: **proposal in a disposable PostgreSQL 18 laboratory**. This checkpoint improves one concurrency property that the canonical SQLite queue does not enforce across independent processes; it is therefore NOT claimed as exact SQLite behavior parity.

## Problem

The current `ConversationStore.cancel_cognitive_turn(..., speech_only=True)` marks existing queued/claimed segments canceled and attaches `voice_cancelled_turn` metadata. A later `enqueue` scans canceled rows and honors that marker. However, if a stop is committed before the first segment of a turn is enqueued, no canceled row exists, so a late segment can be queued after the stop. Concurrent PG workers make this race significant.

## Proposed fix, laboratory only

New `amora_pg_v18_lab.speech_turn_stops` table provides a durable tombstone keyed by cognitive `turn_id`. It is intentionally an **extra experimental table**, not represented among the canonical 51 SQLite logical tables or the v18 import manifest.

`postgres_voice_stop_lab.py`:
- `enqueue_guarded`: inside a transaction, INSERT-ON-CONFLICT to ensure the per-turn lock row exists; SELECT FOR UPDATE serializes concurrent enqueue/stop. If the turn was stopped, the model is recorded as canceled with the voice-stop metadata before the inner speech queue insert.
- `cancel_guarded`: locks the same row; executes the canonical-style segment cancellation; and, if `speech_only`, sets durable canceled/reason/time in the **same outer transaction**. If the PG commit fails, neither tombstone nor segment transitions may partially commit.
- New enqueue and stop for different turns are independent by turn key. Regular `speech_only=False` cancellation does not create a durable future-stop marker.
- The underlying speech module remains a lab module; no runtime Core code is changed.

Real PostgreSQL 18 integration tests:
1. Stop before first segment, then enqueue — the late segment is canceled.
2. Enqueue before stop — the existing segment and all future segments are canceled.
3. Simultaneous stop/enqueue from two independent PG connections, synchronized by a barrier — the segment is canceled in either commit order.
4. Persisted stop works across connection restart; unrelated turns remain valid.
5. Inject a constraint failure while setting the tombstone, proving the entire cancellation transaction rolls back.
6. Any Aiven/remote PG connection fails before SQL.

## Limits

- These tests establish an adversarial local guarantee, not a full proof for distributed workers, retries, network partitions or unknown commit outcomes.
- An extra table introduces a distinct migration, index/storage cost, cleanup/retention policy and backup/restore requirement; it **must not silently alter schema v18** or be introduced into Aiven until reviewed.
- Versioned tombstones and an explicit stop-revision protocol may be preferable for production. The source's exact user-visible voice rules and idempotency must still be validated end to end.
- This code does not access PCS.4C, Turso, Aiven or Northflank; no Core backend selector or production deployment is changed.

Next release gate: full Core PostgreSQL dialect, authorization and message integration, capacity/connection benchmarks, and operator-approved migration rehearsal with rollback.

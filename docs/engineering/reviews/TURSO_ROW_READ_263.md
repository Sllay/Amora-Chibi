# Turso incident #263 — targeted indexed hotpaths, no schema migration

## Evidence, not assumptions

On 2026-11-17, Turso's `amora-staging` Overview displayed
**532,730,376 rows read, 379,560 rows written, and 90.19 MB**.
The free organization was marked as over quota and the serving Core exited
during the very first `schema_migrations` SELECT with remote code `BLOCKED`.
The independent `amora-recovery-test` displayed 758,119 rows read, so the
production source dominates the shown consumption.

The staging Analytics graph showed a **2026-11-08** spike of **214,680,737
rows read in one day**, before the recent four-day period of manual testing.
This does not establish which individual query produced the November 8 spike.

Screenshots of **Top Queries, Ranked by rows read, Today (UTC)** showed:

| SQL signature | Rows read today | Executions today |
|---|---:|---:|
| `SELECT 1 FROM messaging_outbound_queue ... (queued OR claimed/expired) LIMIT 1` | ~2.12M | ~3,950 |
| `INSERT INTO memory_reflection_jobs ... SELECT ... FROM conversation_messages ...` | ~2.03M | 506 |
| `SELECT id,payload_json FROM events WHERE actor=? AND event_type=? ORDER BY timestamp DESC LIMIT ?` | 36.89K | 258 |
| `SELECT payload_json FROM events WHERE actor=? AND event_type=? ORDER BY timestamp DESC LIMIT ?` | 35.61K | 249 |

The two highest today are responsible for about **4.15 million rows examined**
in that *daily sample*. These cannot be summed as the historical breakdown of
all 532.7M rows; Top Queries was explicitly filtered to Today.

## Remediation in this PR (no DDL, no live Turso access)

1. The remote outbound *empty-queue hint* in
   `MessagingStore.claim_outbound` used an OR expression joining
   `status='queued'` and an expired `status='claimed'` branch. Replace it with
   a **single UNION ALL query** of two independent status predicates. Both
   branches seek the existing
   `idx_messaging_outbound_claim(platform, account_scope, status, created_at)`
   index; still one remote SQL read, zero transaction writes on an empty queue.
   The authoritative transactional claim, lease and duplicate-send protection
   are unchanged.
2. `ReflectionQueue.reconcile_missing` and `historical_audit` first filter
   `conversation_messages` using an indexed interval on the existing UNIQUE
   `dedupe_key`: `>= 'cognitive-input:' AND < 'cognitive-input;'`. The
   exact equality `dedupe_key='cognitive-input:' || turn_id`, remote-processing
   policy, privacy, metadata, invalidation and anti-join guards remain
   mandatory. This avoids a full scan of unrelated conversation messages.
   The hot background crash-gap sweep changes from 30s to **120s**.
   Ordinary `enqueue()` still awakens the in-process worker immediately;
   durable leases and cross-instance polling continue at the existing **10s**.
   A missing job after a crash remains recoverable on startup or the next
   bounded 2-minute sweep; no preexisting history is silently discarded.
3. A SQL regression test uses v10/v13 schema DDL and SQLite
   `EXPLAIN QUERY PLAN` to confirm that both outbound UNION branches and the
   reflection candidate prefix range have **indexed searches**, not whole
   table scans. It also checks queued and expired-claimed behavior. Existing
   C08 tests exercise wakeups, owner memory, offloaded Turso polling, and
   interrupted write recovery.

## Conservative limits and follow-up

- This does not purge memories, reset billing, switch providers, create a
  database, or bypass a Turso server quota. It does not claim to explain
  historical **Nov 8** usage without period-matched Top Queries.
- **No schema v19 opt-in or migration** is involved; production v18 remains
  the runtime default. These are query and timing changes only.
- After CI green and manual Northflank deployment, compare *new daily* Rows
  Read / Queries for the affected signatures against a similar workload,
  plus memory write/recall, a crash-gap reconciliation drill, and outgoing
  Discord replies. Do not use the already-blocked Turso account for load tests.
- For historical attribution, collect November 8 Top Queries if the Turso UI
  supports historical date selection, and investigate any expensive memory
  rank/FTS or backup source scans that remain. Escalate account unblock/reset
  to Turso support or await documented quota reset; do not buy a plan or
  change billing without operator approval.

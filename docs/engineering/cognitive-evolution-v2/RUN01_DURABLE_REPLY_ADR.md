# RUN 01 A–B — Durable reply jobs: ADR candidate and implementation evidence

Status: PROPOSED / PARTIAL. Scope: SQLite candidate only, no runtime activation.
Parent: PLAN.md / RUN01_HANDOFF.md / issue #376. Base main 079c42e.

## Why not just extend timeout or reuse reflection jobs?
Reflection and reply generation have distinct lifecycles, authority and
delivery requirements. Existing messaging_inbound_generation already owns
the prepared-outbound fence; it is not a durable pending-work index. Keep
that fence authoritative until integration, then use the **same owner token**
or an atomic shared ownership check when writing outbound. No independently
competing leases may permit stale writes. The candidate repository is not
yet attached to that fence. Do not expose its methods as public actions.

## Candidate state and source ownership
messaging_reply_jobs references the already persisted canonical input ID
(one per turn). No copied text, image bytes, signed URL, prompt, identity
profile, credentials or generated response. Only Core-authored input metadata
(reply_enqueue_requested, reply_outbound_id, existing session/source/revision
metadata) permits enqueue/reconcile; the marker must be written only after
verified DM authorization. The current producer does not write this marker.
A canonical input may contain transformed display/perception text; integration
must preserve exact original message in existing audit/source and establish a
reconstructable sanitized transport reference, not assume it can reconstruct
the whole inbound from the candidate input. Authorize again before provider
invocation, on resume and before delivery; stored consent alone is insufficient.
Images and voice need explicit recovery contracts; no raw attachment persistence.

Transitions: pending -> running; running -> retry_wait (bounded delay),
blocked (specific configuration/context/authorization cause), ready (expected
durable outbound exists), or cancelled (source invalidation). Expired running
leases reclaim the same turn. ready -> sent only by observing actual outbox
sent status. blocked configuration/context can resume with operator approval
and reauthorization; authorization revoked cannot use generic resume. A
blocked turn does not freeze newer messages forever; resumed work must not
overtake already delivered conversation. RUN 06 handles continuation semantics.
One active job/session, initial global running limit 1, controlled by DB
transaction; queue uses canonical input timestamp/turn ID tie-break provisionally.
Final session ordering needs native causal ingress sequence before activation.
No silent job deletion on capacity; admission/backpressure before acceptance
and retention/cleanup of terminal jobs remain part of RUN 01 integration.
Source deletion cascades candidate job, with existing tombstone/invalidation
before destructive removal. Sent replies remain historical delivery facts.

## Storage and migration
Schema 18 default, schema 19 already separately gated. Candidate DDL never
auto-applies, no version bump or production schema change. SQLite repository
uses existing database clock, RLock and BEGIN IMMEDIATE; no await/provider
inside transaction. Current PG18 backend only accepts exact reviewed SQL.
Next substep: audited PostgreSQL mappings and disposable PG18 lab, then
versioned migration with schema/import/backup/restore/rollback inventory,
compatible old/new deployments and migration approval. Never silently apply
DDL or switch backend. Do not choose next schema number before reviewing
existing 19 candidate and exact production state.

## Integration plan that remains mandatory
- Ingress producer: revalidate identity, consent, attention and local controls;
  record original and trusted marker before ACK, reconcile crash gap; preserve
  audit and reflection dedupe; persist only necessary sanitized references
  for rebuilding requests. No new HTTP success without durable acceptance.
- Claim/dispatch: coordinate candidate lease with existing generation lease,
  outbound fence, memory invalidation and outbox, recover prepared responses
  before re-calling provider. Renew ownership atomically or reject stale
  writes. On crash between outbox and ready, reconcile deterministic outbound
  first; if already sent, record delivery without regenerating.
- Classify transient vs permanent; defer retry with persistent timestamps.
  RUN 02 adds adaptive provider recovery. Do not feed operational fallback
  into social history as real answer, or create retry loops around local
  control/suppression. Keep current synchronous mode while flag off.
- Lifecycle: start/stop dispatcher in existing Core lifecycle, remote storage
  lane with separate owned connection, no blocking event loop or network
  transaction. Poll/bounded wake, fairness, invalidate/explicit cancel and
  revocation; reconcile ready with failed/cancelled/sent outbox.
- Transport: ACK queued distinction + typing/progress, existing outbox/nonce,
  duplicate/edit/delete handling, compatibility and backpressure. Verify
  worker code path and routes; no concurrent Discord gateway.
- Candidate tests are not full D01–D16 certification. No live deployment yet.

## Changes in initial slice
- c1fe272: candidate DDL, check constraints, indexes and source ref.
- 7b6f3bd: SQLite candidate repository, reconcile, claim, renew, defer, block,
  resume, invalidation, outbox observation.
- b7928c4: correct input deletion FK to CASCADE instead of RESTRICT.
- 52bd482: seven test functions covering actual Core SQLite storage, duplicate,
  marker crash gap, stale lease, ordering, concurrent claim, invalidation,
  delayed retry, ready vs sent, and revoked state.
- e4c92c6: prevent same-session active claim after a blocked older turn resumes
  while a later turn is running. Still require cross-backend integration tests.

No production data, secret, config, schema or deploy changed. CI results and
subsequent corrections must be recorded in RUN_LOG.md. Full gate remains open.

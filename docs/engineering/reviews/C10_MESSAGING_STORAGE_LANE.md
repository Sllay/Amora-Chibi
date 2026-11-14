# C10 — Remote DM messaging storage lane

## Scope and invariants
- C09's single-owner executor is now reused by independent cognition and
  messaging connections. Every lane serializes synchronous DB operations;
  cancellation joins any in-flight operation before releasing ownership.
- Remote-processing-enabled platform DMs migrate generation claim/release,
  prepared-reply read, inbound audit read/write and generated outbound
  staging/persistence. AUTH/SECRET, groups and local-only paths remain
  synchronous. Explicit memory control persistence and prepared replay
  authorization remain on the original Core path.
- Routing, identity, privacy decisions, appraisal, cognition and provider
  calls are not executed in the messaging thread. No SQL schema changes.
- Preserve the current token/expiry/invalidation SQL conditions, deterministic
  IDs, and stage-before-enqueue recovery. A cancelled in-flight claim releases
  only its own token. A write failure is not automatically replayed. Prepared
  response recovery still avoids a second provider inference.
- New asynchronous boundaries recheck invalidation before replay/duplicate
  recovery and after the inbound audit. The normal new-message path adds one
  SQL recheck; the existing provider and outbound gates remain authoritative.
- ASGI lifespan starts the lane and joins owned inbound tasks before closing
  its connection. No model, prompt, context budget, provider timeout or
  memory-policy changes.

## Validation
The C10 tests cover independent ownership, cancellation after durable claim,
recovery after stage/insert failure, invalidation during fresh/duplicate audit
and trace consistency. The synthetic A/B compares identical fresh databases,
C09 cognition lane active in both, 5 ms added per traced transport request,
one warmup plus three measured DMs per arm. It measures owner-thread SQL calls
separately from total SQL and reports wall/loop metrics without asserting
a production wall-time speedup.

Production validation is still pending. Compare repeated comparable DMs using
`performance_trace kind=critical_path`, `numes_stream_finished`,
`numes_stream_diagnostics`, and `numes_stream_loop`.
The new lane metrics are inclusive; per-stage SQL attribution belongs to the
innermost span, not all descendant operations. Appraisal, context state
mutation, event subscriptions, segment persistence, usage ledger and reflection
commit can still block. The Numes benchmark is independent and opt-in.

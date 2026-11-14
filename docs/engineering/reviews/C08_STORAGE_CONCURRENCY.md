# C08 — storage contention and provider connection reuse

Production observations (2026-11-14): three turns after deploy had context
compile 4.74–4.88 s, total generation 23.74–39.86 s, Numes 13.03–26.85 s,
turn-wide max loop lag 10.13–10.21 s. Reflection accepted a memory on turns
one and three (27 pipelines vs 15 when no memory accepted). These are
correlations, not a controlled A/B test. A recovered transport failure is not
proof a prior SQL operation committed or rolled back.

## Implemented sequence

1. Reflection enqueue/operator retry wakes the local consumer after durable
   insert/update. The worker still polls every 10 s if idle and reconciles at
   startup and approximately every 30 s (plus processing/scheduling delays).
   No historical import, lease rules, provider gates or dedupe changes.
   Other replicas, delayed retries and crash gaps use periodic recovery.
2. Remote outbound empty check is one SELECT without explicit transaction.
   Nonempty polls incur one additional read before the original transaction:
   it rechecks candidates, disclosure and lease conditions. An enqueue
   concurrent with an empty check is available on the next poll.
3. Only background remote reflection reconcile/claim moves off the event loop.
   One consumer awaits each DB-only poll; existing store RLock owns the
   connection for each operation. No transaction spans an await. Cancellation
   joins the thread and returns a freshly claimed lease through normal retry.
   Direct process_once and SQLite retain the existing synchronous path.
   Shared lock contention and the main cognitive prepare/commit path remain;
   this is NOT a full async storage migration. Do not infer a <100 ms
   turn-wide lag from this change. Memory gating and persistence are unchanged.
4. Numes uses a lifespan-owned HTTP pool on the serving event loop, closes it
   on API shutdown; direct calls/CLI use scoped clients. Same endpoint,
   credentials, JSON request, deadlines and no new retry. HTTPcore trace
   measures observed TCP/TLS spans, attempts and trace events only; no trace
   info objects or payloads retained. TCP connect timing includes DNS as
   observed by the transport; it is not isolated DNS timing. No connect event
   with other transport events suggests reuse but is not an unconditional
   proof (proxy/custom transports may differ).
5. Offline tests exercise wakeup, crash-gap recovery, remote empty claim,
   offloaded poll cancellation and connection lifecycle. Existing tests cover
   durable retries, ambiguous SQL outcomes, privacy, dedupe and conversation
   concurrency. Physical validation remains a release gate, not a CI claim.

## Physical validation

Deploy Core revision and collect three matching numes_stream_* records plus
performance_trace per turn. Compare first post-deploy and warm turns with
equivalent inputs, using multiple samples. Track context, generation,
turn-wide/stream lag, memory acceptance, remote calls, outbound claim/ack,
TCP/TLS and recovered/poisoned connection counts. A persistent pool may save
handshakes only when the peer keeps the socket open. No model/personality,
context budget, memory acceptance policy, timeout or schema changes.

# C11 — Dedicated text finalization, first production-safe slice

## Checkpoints
- C11.0: compact numeric `critical_prepare` and `critical_finalize` trace records, retaining the original traces. SQL attribution is innermost-span only.
- C11.1: an internal task-local receipt allows context preparation to skip the duplicate observation only when the messaging caller has already completed the same request's durable input persistence. No client metadata grants the receipt. Other paths retain input persistence.
- C11.2: validated remote private text without a voice callback or speech device uses a dedicated finalizer. For fresh responses with at least three speakable segments, the canonical segment, conversation message and journal rows are inserted in batches inside one transaction on the cognition-owned connection. Both response-planned journal entries are kept and written together in one atomic SQL statement. The speech plan already compiled by the structured validator is reused.
- C11.3: regression tests cover exact segment/history links and flags, journal contracts, SQL round trips, rollback on second-table failure, duplicate delivery, and source invalidation before transaction. Short/partial text and voice retain the legacy segment path.

## Invariants and tradeoffs
The user-visible text, model, context, identity, personality, memory policies, timeouts and speech normalization are unchanged. No schema migration. This is a dedicated **text finalization** path, not deletion of the historical speech segment schema. Existing history consumers still see the same records. The transaction rejects a stale transport revision and does not overwrite existing rows. Unknown write/commit outcomes are never retried automatically; recovery remains on the existing durable path. Explicit cancellation joins the worker, then settles committed rows using the established cancellation path.

Five fresh segments previously cost 15 segment/message/journal pipelines; the batch costs up to 8 including guard reads and transaction controls (6 without a native source guard/extra close when the server closes its transaction stream). The two final journals cost one pipeline rather than two. The receipt avoids duplicate input read and journal write. These are operation-count expectations verified by offline regression; production seconds depend on RTT and concurrent work.

## Remaining checkpoints
Reflection scheduling and stateful emotion/drives/memory optimization are **not** included in this slice. They require separate ordering and recovery validation, especially for consecutive messages and explicit memory requests. Actual Numes/Northflank latency is not validated by CI. The independent opt-in Numes benchmark remains available; no production traffic or credentials are used in these tests.

# C01 — Performance trace end-to-end

## Contract

Diagnostic-only instrumentation. No changes to the model, V3, ACIR decisions,
context budgets, memory retrieval, timeout, delivery semantics or storage schema.
Branch only; do not deploy before CI and operator review.

### Logs

- Core: logger `amora.performance`, message `performance_trace`, kind
  `messaging`, `reflection`, `reflection_idle`, `outbound_claim`, `outbound_ack`.
- Discord: `PERFORMANCE_TRACE stage=inbound|claim|send|ack outbound_id=...`.
- Join Core messaging/reflection by deterministic internal turn UUID; join Core
  messaging/outbound/Discord by internal outbound UUID. No native IDs, message
  text, prompt, reasoning, SQL, parameters or credentials in the new logs.
- AUTH/SECRET owned messaging turns are skipped. Reflection traces are separate
  from the social turn and report processed attempts, not successful memory writes.

### Reading the metrics

`turn_total_ms` includes the owned inbound path including lease release.
`stage_*_ms` are inclusive and may overlap or nest; do not sum them.
`stage_*_sql_ms` and calls attribute each remote pipeline to its innermost
stage. `remote_sql_ms` is summed pipeline time (including transport and
response decoding, not a measure of server CPU), with read/write/transaction
breakdown. `remote_sql_statements` counts logical execute requests before
adapter-added PRAGMA. Repeated shapes are transient exact SQL hashes, not
evidence of redundant data access; no hash or SQL is emitted.
`lock_wait_ms` and `lock_held_ms` are summed across instrumented stores;
nested locks can overlap. `event_loop_lag_max_ms` samples scheduling delay
at 100 ms during the owned turn, not attribution of individual CPU blockers.
Numes timing fields reflect the last attempt; `stage_provider_attempt_ms`
sums attempts, including any bounded repair. Worker durations use monotonic
time; Core and worker times are correlated, not added indiscriminately.
Empty claim/poll operations are logged only when >=1000 ms.

## C02 measurement gate

Collect a baseline of ordinary DMs, explicit recall, long/complex messages,
concurrent DMs and reflection overlap, cold and warm runs. Compare SQLite
and libSQL using identical synthetic fixtures and deterministic fake
realization; record p50/p95, remote calls, stage timing, loop lag, locks,
outbound send and ACK. Separately collect Northflank CPU, memory, restarts,
region and network. No production inference is needed for C01 unit tests.
Do not infer server execution time, network RTT, or a proven bottleneck solely
from the inclusive metrics. No automatic performance changes are part of C01.

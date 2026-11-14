# C09 — Cognitive critical path isolation

Production C08 traces: context compilation ~4.8 s, turn-wide maximum event-loop
lag ~10–11 s; Numes requests varied from 13 to 59 s, with a 503 after 43.77 s.
These are separate issues; do not infer model compute time from local SSE clocks.

## Checkpoints
1. C09.1: bounded single-thread cognition storage lane, separate remote connection,
   explicit operation allowlist, copied tracing context, cancellation joins an
   already-started operation. No transaction spans an await. No automatic SQL
   replay after an uncertain result. API lifespan owns and closes the lane.
2. C09.2: shared canonical compiler steps, synchronous legacy API retained;
   remote DM path yields between stateful operations and reads history through
   the independent lane. State mutations (emotion, drives, memory ranking and
   access updates) remain on the event-loop owner. Compile lock serializes
   migrated DM snapshots; this is not a database-wide snapshot transaction.
3. C09.3: input, decision and structured generation journal persistence through
   the lane; immutable replay snapshot preserved. Explicit memory controls,
   group and local-only turns retain synchronous semantics. Source revision is
   rechecked before each remote attempt after the new asynchronous boundaries.
   No provider, profile, context limits, timeout, validation, memory policy or
   delivery contract changes. Shutdown joins owned messaging tasks.
4. C09.4: compact critical_path performance log, pre-cognition/appraisal,
   cognition, compile, storage lane, validation, provider and observed lag
   phases. Lag phase denotes the phase at callback resumption, not definitive
   attribution of the stall's beginning. Stage timings are inclusive.
5. C09.5: offline regression and standalone Numes benchmark. The benchmark
   uses synthetic small and production-sized (~35 KB) inputs, same model,
   route flags, SSE/JSON and 75 s attempt budget. It does not bootstrap Core
   or read real history, memories, or databases. It reports safe numeric
   measurements only. It requires explicit --live and credentials, never runs
   in CI. Token counts must be read from provider usage; synthetic text is
   not equivalent model workload.

## Physical validation
After deploying Core, compare repeated comparable DMs, first after deployment
and warm, with `performance_trace kind=critical_path`, `numes_stream_finished`,
`numes_stream_diagnostics`, and `numes_stream_loop`. Confirm identity,
one response, persistent memory and no fallback regression. Compare
turn-wide and stream lag, not only provider elapsed time. Remaining synchronous
operations can still block: initial routing/appraisal, stateful compiler steps,
usage ledger, validated segment persistence, event subscribers, outbound and
reflection commit. C09 is deliberately incremental, not a full async database
migration and not a promise of <100 ms physical lag.

Run the independent benchmark in an authorized environment with the same
network region as Core when possible:

```sh
python -m amora.cognition.benchmark_numes --live --samples 3
```

Requires installed Amora package or its core directory on PYTHONPATH, plus
AMORA_SECRET_NUMES_API_KEY or NUMES_API_KEY already in the environment.
No secrets in command lines or logs. Six sequential requests, no retries,
zero-price route constraint retained. Compare small/large results and errors
across samples; no live benchmark has been run by CI.

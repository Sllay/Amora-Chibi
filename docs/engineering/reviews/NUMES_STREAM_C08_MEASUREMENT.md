# C08 — Numes stream latency decomposition

## Motivation

Northflank turn 15652abf-34ae-545e-bdf9-86ab33ccccd4 (2026-11-14):
Numes 73,525.85 ms, first event 16,987.50 ms, first content 37,815.18 ms,
turn-wide event-loop lag 10,179.509 ms. The user reported five mobile Discord
lines. These are observations, not a proof of upstream tokens/second.
The old first-content-to-completion interval conflates generation and stream
termination; the turn-wide lag does not say where during the provider call
a stall happened.

## Contract

Diagnostics only, no changes to model, prompts, payload, routing, timeout,
memory, validation, streaming delivery or persistence. No extra provider call.
No raw reasoning, conversation text, headers, credentials or arbitrary usage
objects are logged. Existing SSE validation remains authoritative.

Correlate three log messages by request_id:
- numes_stream_finished: existing outcome and first-event/first-content timings.
- numes_stream_diagnostics: SSE data event counts, largest event/content gaps,
  reasoning/content delta counts and character counts, first/last timestamps,
  finish-reason and DONE timings, surface text character count, optional
  provider-reported prompt/completion/reasoning tokens.
- numes_stream_loop: 100 ms scheduling sampler during the same attempt,
  counts, max/sum lag and maxima observed before/after first content.

The turn trace also includes numeric numes_diag_* fields. These describe the
last attempt; correlate separate request IDs for retries. All values are local
client observations. Missing provider token fields stay absent; characters and
SSE events are not tokens. completion_tokens may include reasoning according
to provider accounting; do not assume subtraction is a valid visible token
count without confirming semantics. Reasoning character counts are received
text/summary only, and aliases are counted once. Surface chars are the parsed
model move text before validation/Discord formatting. Reasoning can occur after
content starts (negative last_reasoning_to_content_ms means overlap).
content_span_ms is first to last content, not first to DONE; finish_to_done_ms
isolates terminator delay. SSE gaps include server buffering, network and local
scheduling. Loop lag is sampled locally; phase means at observation, not exact
attribution of a stall's start. Do not subtract lag sums from elapsed time.

## Physical gate

After Core is deployed, collect these three records for the same request,
including a short ordinary DM and longer dialogue. Compare output volume,
content span, last-content-to-DONE, and per-attempt loop lag. Only if the
provider reports suitable token accounting may we calculate a meaningful token
rate; otherwise use characters and events as proxies. A long gap with low
local lag is evidence for upstream/transport investigation, not proof of
model compute alone. No real Numes calls are made by CI.

Remaining work: reduce sync storage blocking/idle polling (C08.1–3), repeat
physical measurements and then decide whether upstream routing or generation
configuration needs any adjustment. Do not alter current model or timeouts
based on one turn.

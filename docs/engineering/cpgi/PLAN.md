# CPGI — Core Performance & Generation Integrity
Status: RUN01 static diagnosis; live reproduction pending.
## Rules
Preserve persistent memory, identity, personality, tools and visual assets. No production configuration changes or merge before certification. GLM remains primary. Checkpoint each run; full regression in RUN04.
## Runs
1. Audit transport, context, validation, delivery; reproduce duplicate call and capture rejection code. Compare direct API, adapter, full context and complete pipeline with matched settings. Never log credentials or private prompt/output.
2. Adaptive context selection and provider-specific budgets; preserve recall evidence and mandatory contracts; prevent avoidable HTTP 413. Validate against baseline.
3. Generation integrity: eliminate unnecessary regeneration, safe local recovery, incremental validated delivery, causal grouping, bounded activity-aware deadlines, event-loop profiling and fallback deduplication. Reuse existing infrastructure.
4. Certification: matched workloads, latency distributions, memory/personality/tools regressions, CPU/RAM, failure recovery, Discord delivery; review before promotion.
## RUN01 checkpoint
Production trace 2026-11-28 UTC, turn 83a3117a-0a34-514d-83e6-4632b8f1d8b8: GLM HTTP 200, first reasoning 3.058s, first content 20.094s, complete 46.496s, 8061 prompt tokens, 746 completion tokens. Second identical-sized realization starts immediately; caller cancels after 13.399s, shared attempt 60s. Groq 413, Gemini final; turn 72.735s. Event-loop max 5.201s, 19 CPU throttle events. Separate recall turn local MEMORY_RECALL_SEARCH_INCOMPLETE.
Source: providers_numes.py:377-389 marks completed only after stop/DONE and JSON object parse. provider_resilience.py:167-210 validates mapping then retries same context on RealizationError; :229-234 configures Numes max_retries=0, max_validation_retries=1. resilience_runtime.py:25-32 wraps registered adapters. This establishes a concrete retry mechanism consistent with trace; exact rejection code not recorded in supplied logs and not proven. No claim that valid schema was discarded. Need capture validation error before retry and reproduce with synthetic fixture. Existing benchmark_numes.py is starting point. No runtime changes yet.
## Acceptance
RUN01 remains open until automated reproduction and matched live measurements or explicit documented access limitation. Targets for later runs: zero unnecessary regenerations and avoidable 413, p95 loop lag below 100ms, simple-turn 5–15s subject to provider latency; no cognitive regression.

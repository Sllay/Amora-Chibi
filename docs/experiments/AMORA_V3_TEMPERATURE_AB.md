# Amora V3 — Dynamic sampling A/B (operator-only)

**Status:** isolated experiment; no production sampling or provider promotion.

The NVIDIA Mistral Large 4 provider currently fixes `temperature=0.50` and `top_p=1.0`. This laboratory leaves the production provider alone and lets the operator compare two explicitly chosen profiles using the **same exact user-authored English V3**. This is useful for investigating memorization of V3 calibration examples and improving colloquial improvisation.

## Commands

Simplest (default control 0.50/1.00 vs experiment 0.75/1.00):

```text
providers experiment amora-v3-temperature --case safada
```

Choose the test profile (the control stays 0.50/1.00):

```text
providers experiment amora-v3-temperature --case safada --temperature 0.85
providers experiment amora-v3-temperature --case sacanagem --top-p 0.95
providers experiment amora-v3-temperature --case boca_suja --temperature 0.90 --top-p 0.95
```

Select both profiles to compare 0.75 versus 0.95 at the same `top_p`:

```text
providers experiment amora-v3-temperature --case safada --temperature-a 0.75 --temperature-b 0.95 --top-p-a 0.95 --top-p-b 0.95
```

Select only `top_p`, keeping temperature fixed:

```text
providers experiment amora-v3-temperature --case sacanagem --temperature-a 0.75 --temperature-b 0.75 --top-p-a 1.0 --top-p-b 0.90
```

Supported flags (order-independent): `--case ID` (required); `--temperature X` alias of `--temperature-b X`; `--top-p Y` alias of `--top-p-b Y`; `--temperature-a X`, `--top-p-a Y`, `--temperature-b X`, `--top-p-b Y`.

- A defaults: temperature 0.50, top_p 1.00.
- B defaults: temperature 0.75, top_p 1.00.
- NVIDIA Mistral Large 4 bounds: **temperature 0–2 inclusive**, **top_p greater than 0 and at most 1**. At most four digits after the decimal separator; both decimal dot and comma are accepted.
- Any unknown/repeated/invalid flags, `NaN`, `inf`, scientific notation, out-of-range numbers and **identical sampling profiles** fail before an NVIDIA call.
- Can change both axes, but then any behavioral difference cannot be attributed to only one parameter. NVIDIA documentation recommends varying temperature and top_p separately for interpretable results.

## Invariants

- Same hash-pinned V3 in both variants; no editing user-owned V3 prompt or V2.
- Same Core snapshot, DialoguePlan, prompt, history, NVIDIA Mistral model, `reasoning_effort=low`, token budget, `frequency_penalty=0`, `presence_penalty=0` and transport.
- A/B candidate order randomized; the report displays the two chosen parameter profiles **without identifying which candidate received which one**, until base64 reveal after scoring.
- Two sequential inference calls maximum, without retry or fallback. Stop on HTTP 429.
- Strict `RealizationValidator`: distinguish invalid JSON from valid JSON that violates the Core.
- No production routing or sampling change, no memory or self-choices persistence, no tool action or promotion.

## Calibration-copy diagnostic

The runner compares the response against V3's `GOOD:` examples (§7) and reports the **longest consecutive shared word sequence**. Seven or more consecutive matched words raises a lexical copy signal. This is a heuristic, not proof of plagiarism; human scoring is still required. Observe language naturalness, adult humor, spontaneity, JSON validity, invented events and latency.

## First probes

```text
providers experiment amora-v3-temperature --case safada --temperature 0.75
providers experiment amora-v3-temperature --case safada --temperature 0.95
providers experiment amora-v3-temperature --case sacanagem --temperature 0.90
providers experiment amora-v3-temperature --case boca_suja --temperature 0.85 --top-p 0.95
```

Each command initiates two NVIDIA calls (unless rate-limited). Run one job at a time after the main-branch deployment. Repeat important cases to avoid overinterpreting single random outcomes. Live provider results and the operator's human ratings are necessary for a recommendation.


## NVIDIA stream timeout versus Core rejection (2026-11-08)

The previous experiment runner used the production idle timeout of
**55s**. Two attempts returned `EXPERIMENT_PROVIDER_FAILURE` after
55.09s and 55.06s with **no provider message**, yet Shell displayed
`REJEITADO pelo Core`. This was an **incorrect status label**:
`RealizationValidator` never ran. Two identical elapsed times strongly
suggest NVIDIA SSE idle/read timeout, but do not prove whether the
failure was while waiting for headers, for the first event, or after
partial generation.

Both operator-only V2×V3 and V3 sampling tests now use an idle
timeout of **100s** and an independent total cap of **225s per
call**. Two sequential calls are within the 650s admin deadline.
The real production Mistral continues at idle **55s**, total **185s**.
Do not interpret larger limits as a guarantee of success, and do
not automatically retry stalled generation.

The runner records:
- `validation_status=not_run`, `error_code=EXPERIMENT_NVIDIA_TIMEOUT`,
  sanitized `failure_stage` (`waiting_headers`,
  `waiting_first_event`, `reasoning_only`, `visible_partial`) and
  `timeout_kind` (`idle` or `total_safety`);
- only safe event counters and timing markers; never private reasoning,
  generated fragments, prompt content, auth credentials or exception bodies;
- transport/HTTP failure separately from invalid JSON, Core rejection
  and validated output.

If the next run still times out, inspect the stage:
`waiting_headers` or `waiting_first_event` indicates upstream
latency/availability; `reasoning_only` indicates no visible answer
yet, even if the model was reasoning; `visible_partial` means an
incomplete structured response was discarded correctly. An HTTP
failure or 429 requires a different diagnosis. No rejection of vulgar
language can be inferred without visible model output.

All prompts remain byte-for-byte unchanged. Live NVIDIA certification
and deployment of the updated main branch remain separate steps.

# Amora V3 — Temperature 0.50 vs 0.75 (operator-only)

**Status:** experiment, not a provider setting change or automatic promotion.

## Why this experiment

The NVIDIA Mistral provider currently sets **temperature=0.50** in
`core/amora/cognition/providers_nvidia.py`. The V2×V3 social tests
therefore inherited 0.50 for both variants. More variation may help
the user's V3 be less reliant on memorized calibration examples, but
increased temperature does not guarantee originality and may decrease
JSON compliance or factual fidelity.

## Command

```text
providers experiment amora-v3-temperature --case safada
```

Other cases: `sacanagem`, `boca_suja`, `mente_poluida`, `duplo_sentido`,
`humor_acido`, `apoio_serio`, and all existing V2×V3 cases.

## Control and safety invariants

- **A:** original, hash-pinned V3 English prompt, temperature **0.50**.
- **B:** same original V3, temperature **0.75**, user-requested roleplay trial.
- The prompts, Core snapshot, DialoguePlan, user text, model, history,
  top_p=1.0, frequency_penalty=0, presence_penalty=0,
  reasoning_effort=low, and max_tokens are identical across candidates.
- No V3 prompt edits; the full file is loaded using existing integrity checks.
- Neither production provider nor routing defaults are changed.
- Two sequential calls maximum; no retry, fallback or paid upgrade.
- HTTP 429 stops the remainder; no secret or remote response body in errors.
- JSON syntax and Core schema/semantic rejection are recorded separately.
- Memory, personal choice, identity, relationships, production and tools
  are never altered by these synthetic cases.
- Random blind order `candidate-1/2`; reveal base64 mapping only after scoring.
- Under this design, a single case cannot prove a statistical win.

## Example-copy diagnostic

The runner extracts only V3's lines labelled `GOOD:` in the calibration
section and compares consecutive normalized word runs against the
speech text from the current realization. It reports:

- `max_consecutive_example_words`
- `example_copy_signal` when at least 7 consecutive words match.
- `signal_threshold_words` and metric scope.

This is a narrow **lexical-overlap flag**, not a proof of plagiarism.
A legitimate phrase can overlap, and semantic imitation will not
necessarily trigger. Novelty, timing, creativity and naturalness
still require blind human review.

## Suggested sequence

Run one command at a time after deployment:

```text
providers experiment amora-v3-temperature --case safada
providers experiment amora-v3-temperature --case sacanagem
providers experiment amora-v3-temperature --case boca_suja
providers experiment amora-v3-temperature --case humor_acido
providers experiment amora-v3-temperature --case apoio_serio
```

Review latency, JSON validity, original punchlines, hallucinated
context details, tonal heat and copy diagnostics. If 0.75 is promising,
a *separate experiment* can compare 0.75 vs 0.95 with equal settings.
Do not assume that the highest temperature is optimal for a persona.

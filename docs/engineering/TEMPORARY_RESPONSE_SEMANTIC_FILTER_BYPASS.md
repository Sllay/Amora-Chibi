# Temporary bypass — conversational response semantic filter

**Status:** disabled by default in production until the output gate can be corrected.

## Operation

- `AMORA_RESPONSE_SEMANTIC_FILTER_ENABLED=0` or unset (default): accept schema-valid, non-empty provider `RealizationFrame` speech even if its move kinds, questions, help offers, closing mode or DialoguePlan depth do not match the Core's conversational plan.
- `AMORA_RESPONSE_SEMANTIC_FILTER_ENABLED=1` (also true/yes/on): restore the original strict conversational validation.
- The switch is read at runtime for production responses and at provider-wrapper registration for repair behavior; **restart the service after changing it** to keep both aligned.
- The unchanged strict `RealizationValidator.validate(...)` API and explicit experiment/certification calls still enforce the original contract.

## Scope / non-goals

- Keep provider-generated *words* in `display_text`. No keyword censorship, regex content trimming, semantic rewrite, forced replacement or fallback just because the answer includes an unplanned question/offer/length.
- JSON/schema validation, basic non-empty text, and provider transport failures are still enforced. Malformed provider output can still be rejected.
- Unauthorized or duplicate `self_choices` are ignored **without rejecting the spoken reply**. Authorized self-choices continue through `commit_self_choices` and its canonical authority checks.
- Privacy scopes, authentication, memory access, relationship/identity authority, tool/action execution permissions and external provider policies are **not** bypassed.
- `SpeechNormalizer` still makes voice-specific presentation changes (e.g., stage cues, emoji, markdown), while preserving the original display text. `SpeechSegmenter` still divides speech into TTS-sized segments. Neither is a content policy filter; both need separate fidelity investigations if speech still sounds fragmented.
- This bypass makes no claim that a hosted LLM will produce answers without its own provider-level refusals.

## Validation

Run:

```bash
pytest -q tests/test_temporary_response_filter_bypass.py tests/test_v10_dialogue_emotion_integration.py tests/test_phase2_v3_staging.py tests/test_ch3_provider_resilience.py
```

Compare the exact provider `moves[].text` with `display_text` and the concatenated speech segments (allowing only documented TTS presentation normalization). Verify the switch can be restored before any rollout.

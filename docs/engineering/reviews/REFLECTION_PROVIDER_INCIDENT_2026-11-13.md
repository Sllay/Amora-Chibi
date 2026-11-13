# Reflection provider incident — 2026-11-13

The observed Core logs show a single durable turn failing on Groq, Gemini and
OpenRouter. No exact HTTP status or invalid field was present. The production
Groq adapter is providers_identity.py (Responses API), not the older Chat
Completions adapter. It previously used the full social context and JSON-object
mode for reflection. A schema/transport mismatch is a plausible contributor,
not a verified live root cause.

## Correction

Durable platform reflection is now source-only extraction. The Core compiles
the current observed message, identity envelope and privacy scope, without
persona, earlier dialogue, unrelated memories or emotion state. The memory
wire schema has a bounded list of proposals, explicit numeric ranges and
required fields. The normal local write/owner/lease gates remain authoritative.
This extraction produces no hypotheses or actions; other non-durable reflection
paths retain their existing contracts. Low-signal inputs may yield an empty list.

Groq Qwen uses the existing Responses JSON-schema interface and disables
reasoning for the extraction. One bounded format repair is allowed. Gemini
uses its JSON-schema generation config; OpenRouter and compatible mesh fallback
use JSON object plus local validation. No conversation-model promotion or
technical/vision model change. The provider error body is never logged.
Reflection diagnostics now include safe status, category, latency, bounded
validation field/type pairs and accepted counts; reflection cooldowns do not
change the social circuit or router quota. A waiting job does not spend a retry
during backoff. Durable provider calls (including repair) have a 16 s limit,
with 48 s total routing budget inside the existing 60 s queue guard.

## Live validation

After CI and approved Core deployment, use the authenticated operator command
`memory reflection probe` to send a single synthetic extraction to Groq
(with at most one format repair). It returns model, success and candidate
count and would-store count, or safe diagnostic metadata; it writes no
conversational memories. A valid but empty/low-signal result does not pass.
Then run `memory reflection status`. If the old turn failed permanently,
use `memory reflection retry d4adb252-d1d4-5f2e-9b87-103d6cca747b` only if it
is actually failed. Existing completed records are not reprocessed.
Use a fresh synthetic fact in a private Discord channel and verify queue status
and authorized recall. A passing mocked transport/CI is not live API validation.

No schema migration, reset, V3 modification, dialogue.py modification or
worker deployment required for this change.

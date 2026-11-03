# ADR-0034 — CH3 provider certification and Mistral Large 4 candidate

- Status: **Accepted for implementation / certification only**
- Date: 2026-11-02
- Scope: CH3 Provider Certification + Resilience
- Dependency: stacked on CH2 PR #123; this ADR does not authorize merging CH3 before CH2 is integrated.

## Context

Amora Core owns identity, self-model, relationships, memory, emotion, world state, DialoguePlan, privacy and action authority. Remote models are replaceable compute surfaces and must not become the source of Amora's identity or policy.

The current social surface actor is Qwen 3.8 27B through Groq. Mistral Large 4 (`mistralai/mistral-large-4-1t-a52b`) is a new multimodal/reasoning candidate available through NVIDIA's OpenAI-shaped chat-completions endpoint. It is interesting enough to benchmark, but recency, provider behavior, latency, conversational permissiveness and hosted-endpoint contracts are not reasons to promote it without evidence.

The existing Cognitive Replay is safe and immutable, but its historical implementation replays `provider.stream(context)` directly. Production social speech now goes through structured `realize(context)` followed by local `RealizationValidator`. Comparing raw stream text would therefore certify a different path from the one that can actually reach speech.

## Decision

### 1. Certification measures the final realization path

Social-provider certification MUST run:

```text
canonical immutable CognitiveContext
  -> candidate.realize(context)
  -> RealizationValidator.parse_and_validate(...)
  -> bounded mechanical score
  -> explicit human social-quality review
```

Certification MUST NOT mutate conversation messages, memory, relationships, world state, emotion, intentions, journal state or provider promotion state.

### 2. Fixed social replay corpus

`SOCIAL_SURFACE_REPLAY_V1` covers at least:

- ordinary pt-BR conversation;
- contextual profanity;
- playful insults/affection;
- broken/absurd humor;
- permitted adult relationship context;
- conversational sexuality context;
- disagreement/conflict;
- self/identity questions;
- relational identity questions;
- heavy subjects mentioned normally;
- a genuine safety-boundary control case.

The mechanical score may flag objective anti-patterns such as generic assistant self-reference, canned moralization and refusal on a probe where refusal is not expected. It MUST NOT pretend to automatically decide nuanced qualities such as humor quality, naturalness, personality fit or whether a refusal is socially well-written. Those remain review dimensions.

### 3. Mistral Large 4 starts certification-only

`NvidiaMistralLarge4Provider` is a candidate adapter only. It MUST NOT be registered by normal `AmoraCore` bootstrap or receive production priority during this stage.

Initial descriptor constraints:

- task: `SOCIAL_DIALOGUE` only;
- provider class: remote ephemeral;
- priority: deliberately negative / non-production;
- promotion status: `not_promoted`;
- secret: `NVIDIA_API_KEY` through the existing `SecretStore` boundary;
- dialogue reasoning effort: `none` for the social-surface benchmark;
- local `RealizationValidator` remains authoritative.

### 4. Do not overclaim hosted structured-output contracts

The Mistral Large 4 model documentation advertises JSON / strict JSON-schema capability. The concrete NVIDIA hosted endpoint contract must still be certified independently before Amora marks that route as `supports_structured_output=True`.

Until an integration test proves the exact hosted request/response contract, the candidate descriptor remains:

```text
supports_structured_output = false
structured_output_status = endpoint_unverified
```

The adapter requests the canonical realization object in trusted instructions, parses JSON locally and rejects invalid output. This is a temporary certification posture, not a claim that prompt-only JSON is equivalent to strict schema enforcement.

### 5. Promotion is task-specific and evidence-based

CH3 may compare Qwen 3.8 vs Mistral Large 4 for social dialogue and Mistral Large 4 vs GPT-OSS for reasoning, but winning one task class MUST NOT automatically promote the model to another.

Promotion requires evidence for the relevant task class, including:

- DialoguePlan fidelity / local validation pass rate;
- pt-BR conversational naturalness;
- personality continuity;
- contextual profanity and humor behavior;
- permitted adult-context behavior;
- assistantism / moralization / inappropriate refusal incidence;
- grounding and identity stability;
- latency and quota/cost behavior;
- provider failure/recovery behavior.

## Consequences

- Amora can benchmark very different provider personalities without delegating identity to a model.
- The benchmark follows the same structured boundary that protects production speech.
- A new provider cannot silently become primary just because an API key exists.
- Hosted capability claims are separated from model-card capability claims.
- CH3 remains safely stackable while CH2 is still draft; merge order is explicit.

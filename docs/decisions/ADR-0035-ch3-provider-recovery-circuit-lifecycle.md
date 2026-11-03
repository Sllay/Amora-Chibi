# ADR-0035 — CH3 provider recovery and circuit lifecycle

- Status: **Accepted for implementation / stacked certification**
- Date: 2026-11-02
- Scope: CH3 Provider Certification + Resilience
- Dependency: stacked on CH2 PR #123; this ADR does not authorize merging CH3 before CH2 is integrated.

## Context

Amora already had two useful but incomplete resilience layers:

1. provider adapters were wrapped by a bounded recovery proxy that could retry transient failures once;
2. the structured cognition runtime maintained provider failure counts and cooldown timestamps before falling back.

That baseline was not yet the full recovery-before-fallback contract recorded in canon. In particular:

- retry delay did not honor HTTP `Retry-After`;
- transport retry had no jitter;
- long rate-limit waits could not be distinguished from short inline recovery budget;
- structurally invalid realization was not given a bounded same-context repair attempt inside the provider recovery boundary;
- cooldown expiry returned a provider directly to ordinary traffic instead of a controlled `HALF_OPEN` probe;
- the existing “circuit” behavior was therefore a timed exclusion, not an explicit `CLOSED → OPEN → HALF_OPEN` lifecycle.

## Decision

### 1. Preserve the existing router and add an explicit circuit registry

CH3 MUST NOT create a second provider router. `ProviderRouter` continues to own provider ranking/quota selection. A separate `ProviderCircuitRegistry` owns only recovery state by provider + task class:

```text
CLOSED
  ↓ qualifying failure
OPEN
  ↓ cooldown expiry
HALF_OPEN
  ↓ one leased probe succeeds        ↓ probe fails
CLOSED                              OPEN
```

The circuit registry is additive to the existing cooldown map so old/hot-reload state does not silently become routable during migration.

### 2. HALF_OPEN uses a bounded probe lease

When an `OPEN` provider reaches its retry deadline:

- one routing decision may expose it as a `HALF_OPEN` probe;
- a short lease prevents concurrent turns from probing it simultaneously;
- the legacy cooldown/quota exclusion is cleared only for that bounded probe;
- success closes the circuit;
- failure reopens it with a new deadline;
- if no turn selects the provider during the lease, another probe opportunity may be issued after the lease expires.

This prevents a cooldown from ending in a thundering-herd return to normal traffic.

### 3. Retry-After is part of failure classification

`Retry-After` MUST support both legal HTTP forms:

- delay-seconds;
- HTTP-date.

A short server-requested delay may be honored inside the same interactive turn when it fits the inline recovery budget. A long delay MUST NOT make dialogue sleep through the provider cooldown; it is surfaced to the outer runtime so the circuit can open and another provider may be considered.

### 4. Transport backoff gets bounded jitter

Transient timeout/network/429/5xx recovery uses bounded exponential backoff with jitter. The exact same immutable `CognitiveContext` is reused for every retry.

Deterministic auth/bad-request failures are not retried blindly.

### 5. Structured realization gets one bounded repair attempt

For providers using the structured social realization boundary:

```text
provider.realize(context)
→ local RealizationValidator
→ [invalid? one same-context repair attempt]
→ valid OR provider structured-output failure
```

If the second result is still invalid, the recovery proxy raises `StructuredRealizationProviderError` with the original realization error code preserved. That failure is classified as:

```text
PROVIDER_STRUCTURED_OUTPUT_INVALID
retryable = false
```

It then reaches the outer provider-failure path, which can reopen `HALF_OPEN` and fail over. It MUST NOT be mistaken for another transport retry opportunity.

### 6. Dialogue circuit is implemented first; task-class expansion stays explicit

The first integrated circuit target is `SOCIAL_DIALOGUE`, because that is the production structured realization path exercised by Qwen today and by the Mistral candidate in CH3.

Reasoning and asynchronous perception may reuse the same registry contract, but they require separate task-class certification and budgets. Social-dialogue success MUST NOT be treated as proof that the same timings or retry policy are correct for reasoning or perception jobs.

### 7. Circuit state is observable, not secret model state

The cognition state surface exposes bounded circuit snapshots containing provider, task class, state, failure count, deadlines and failure code. It does not expose credentials, raw provider payloads or prompt content.

## Consequences

- one transient fault still gets bounded recovery before failover;
- a long `Retry-After` no longer stalls an interactive reply;
- provider recovery does not mutate Amora identity or Core context;
- invalid structured output receives a bounded repair rather than immediate provider switching;
- cooldown recovery becomes controlled and observable through `HALF_OPEN`;
- no second router or provider-authority layer is introduced;
- reasoning/perception recovery remains separately certifiable instead of inheriting dialogue behavior by assumption.

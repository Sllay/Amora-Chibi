# Narrative privacy audit

Scope: prevent cross-audience disclosure without editing the V3 profile or dialogue orchestrator.

## Findings
- The messaging service already supplies channel_type and a transport-scoped session.
- The context compiler previously authorized memories and claims by speaker identity, not response audience.
- Scene visibility was not projected into CognitiveContext.
- Self-model snapshots discard entry-level provenance and may contain private learned state.
- The full expressive profile may contain unrevealed narrative facts. Context filtering alone cannot guarantee confidentiality.

## Classification
Public: approved identity, public personality and already disclosed canon.
Restricted: private dialogue, recordings, unrevealed events, private relationships and derived summaries.
Audience-specific: facts explicitly shared with a defined set of participants.
Internal: profile instructions, examples, operational contracts and unreleased narrative material.
Fan theories are not authorization or confirmation.

## Implemented in this branch
Shared messaging contexts receive public memories and claims only. Explicit same-session group-origin PERSONAL history is allowed; private history is not. Shared self-model projection retains only canonical name; active intention is withheld. Scene visibility is propagated to the existing planner.

## Additional controls and validation

- Shared relationship graphs no longer grant access merely because the current speaker is an endpoint of an unlabeled relationship. Explicit public catalogue edges and authorized public canonical claims remain eligible.
- Derived memory writes with source IDs inherit the strictest source scope, intersect effective audiences, and cannot broaden remote consent.
- Assistant history retains transport origin metadata.
- A deployment-configured literal registry checks destination authorization before structured realization persistence and again before group publication. The registry is not a semantic classifier; empty configuration gives no additional content-specific protection.
- Integration regressions exercise DM history persistence, shared context isolation, public conversational continuity, publication suppression and duplicate handling with synthetic data and a simulated Discord transport.

## Remaining release blockers
- Audit all provider payloads, unlabelled entity metadata, derived memory provenance coverage and non-structured output paths.
- Verify group-origin metadata persistence and continuity across real Discord turns.
- Validate deployment configuration, streaming and fallback paths. Literal matching does not establish semantic confidentiality. Do not claim paraphrase detection without an independently evaluated verifier; prefer source isolation for genuinely confidential data. A full profile supplied to a model can remain a disclosure source.
- Verify latest integration regressions and CI; no deployment or merge until reviewed.
- Verify protected file hashes against base.

Use synthetic secrets in tests and logs. Do not copy actual private dialogue or unrevealed narrative content into this public repository.

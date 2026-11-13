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

## Remaining release blockers
- Audit relationship registry and all provider payloads, derived memory provenance and outbound history.
- Verify group-origin metadata persistence and continuity across real Discord turns.
- Implement an authorized disclosure registry and a pre-publication output guard covering all delivery paths, including streaming and fallback. A semantic guard is not an absolute confidentiality guarantee when the full profile is supplied.
- Run regression tests and CI; no deployment or merge until reviewed.
- Verify protected file hashes against base.

Use synthetic secrets in tests and logs. Do not copy actual private dialogue or unrevealed narrative content into this public repository.

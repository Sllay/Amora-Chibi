# ADR-0013 — Safari/Web as the Primary Iteration Surface

- Status: ACCEPTED
- Date: 2026-10-21
- Owners: BK + AMORA IA
- Scope: development workflow, Web/PWA platform adapter, Android debug freeze, conversational testing

## Context

The Android debug path became a patch loop with a very poor iteration ratio. Small changes to conversation timing, barge-in, VAD or delivery repeatedly required a full Android build, APK transfer/install and physical test before the next hypothesis could be evaluated.

BK explicitly clarified that the current debug APK is not the intended final Amora Android application. The official Android product is expected to be a different client with a different product surface. Continuing to treat the debug APK as the primary laboratory was therefore spending disproportionate time on a disposable delivery surface.

The repository already planned a Web/PWA client for iPhone after V1 voice closure. The Core already exposes reusable contracts for pairing, STT, conversation queue, cancellation and state. Moving the iteration surface earlier does not require moving Amora's identity or canonical state into the browser.

Safari/iOS also requires a secure context for microphone-oriented web APIs. For development, a localhost Core can be exposed through an HTTPS development tunnel while keeping the Core itself bound to `127.0.0.1`.

## Decision

Activate the Web/PWA track immediately and make Safari the primary conversational iteration surface before Android V1 physical certification is complete.

```text
Amora Core / canonical state
        |
        +-- Android debug adapter  -> frozen reference / secondary gate
        |
        +-- Web/Safari adapter     -> primary rapid iteration surface
                                      mic / VAD / STT / dialogue / barge-in
```

### Android debug status

- dev14 remains a reference build and evidence source;
- no more APK-per-small-test loop by default;
- Android-specific work resumes when a behavior is stable enough to justify native integration or when a real Android-only gate is being tested;
- the future official Android app is not defined by the current debug APK UI/runtime shape.

### Web/Safari lab

The first Web lab must:

- be served as a separate platform surface under `/web/`;
- reuse Core pairing/authentication;
- reuse the same Language/Social and Conversation/Cognition pipeline;
- capture microphone audio in the browser and submit WAV through the existing STT contract;
- support local browser-side barge-in and immediate output cancellation;
- expose a manual `Parar fala` failsafe;
- prequeue multiple speech segments in the browser transport to make timing tests cheap;
- expose simple latency/gap diagnostics;
- default development probes to `test_mode=true` so repeated testing does not automatically consolidate memory/relationship/emotion state.

### Voice during the first Web lab

Browser `speechSynthesis` may be used only as an explicitly labelled **debug transport voice** for testing dialogue flow, timing and interruption.

It is not `AMORA_CANONICAL`, does not replace FINAL_B + VoiceSignature V1 Soft, and must not be used to judge canonical timbre.

The canonical direction remains:

```text
spoken text
    -> Voice Worker / portable canonical voice path
    -> FINAL_B + VoiceSignature + bounded emotion
    -> browser continuous audio queue
```

A Voice Worker is the next voice-specific Web step after the transport loop is validated.

### HTTPS development access

For the rapid Safari loop:

- Core remains bound to localhost;
- `cloudflared` Quick Tunnel may expose the local HTTP origin as temporary HTTPS for development;
- the repository provides `tools/web/start-safari-lab.sh`;
- optional protected Quick Tunnels may use `AMORA_ALLOWED_MAIL`;
- this does not approve migrating the persistent Core to Cloudflare or any remote host.

## Why this is not a V1 certification shortcut

This decision changes the **iteration surface**, not the certification requirements.

Android physical certification, canonical voice certification, long-run stability and the final native product remain separate gates. Safari success does not prove Android success, and browser debug speech does not certify canonical voice.

## Consequences

Positive:

- frontend changes become refresh-level iterations instead of APK cycles;
- dialogue, model selection, VAD thresholds, UX and barge-in can be evaluated much faster;
- Core/platform boundaries are exercised earlier;
- the disposable debug APK stops dominating engineering time;
- the future official Android client can consume stabilized contracts instead of inheriting experimental UI/runtime decisions.

Tradeoffs:

- Safari has platform-specific audio lifecycle quirks that must be treated as Web adapter issues;
- secure HTTPS access is required for microphone testing;
- the first Web voice is intentionally noncanonical;
- a server/worker implementation of canonical voice is still required before Web can certify Amora's acoustic identity.

## Revisit criteria

Revisit this decision only if one of these becomes true:

1. Safari prevents reliable microphone/barge-in testing even after a bounded adapter fix;
2. the Web adapter starts duplicating canonical Core state/logic instead of reusing contracts;
3. a stable official Android client becomes faster to iterate than the Web surface;
4. the final product requires a native-only capability that cannot be meaningfully validated on Web.

## References

- `docs/roadmap/VERSIONS.md` — V1.0.1 Web/PWA direction
- `PROJECT_STATE.md` — Web/PWA next platform front
- ADR-0012 — hybrid spoken cognition and barge-in requirements
- WebKit: Web apps on iOS/iPadOS and Home Screen behavior
- WebKit Safari 26 media updates
- Cloudflare Quick Tunnels documentation

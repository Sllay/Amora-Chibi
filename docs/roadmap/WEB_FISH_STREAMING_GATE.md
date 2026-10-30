# V1.0.1 — Web Fish streaming gate

Status: `IN_PROGRESS — CODE/CI GATE PENDING / PHYSICAL SAFARI GATE PENDING`  
Data: 2026-10-30  
Decision: ADR-0022

This checkpoint refines the V1.0.1 Web/PWA line without moving canonical state into the browser.

Implemented on `feat/web-fish-turn-streaming`:

- one `WEB_SAFARI` voice delivery per cognitive turn instead of one Fish request per short `SpeechSegment`;
- cognitive segments remain persisted and auditable;
- authenticated Core → Safari PCM s16le streaming path;
- Safari playback begins from a small bounded buffer while Fish continues generating;
- client backpressure and observable underruns;
- complete-WAV compatibility path remains available;
- Fish→Gemini fallback only before the first Fish PCM is emitted;
- bounded Core emotion/cues → Fish expressive tags;
- no cloud FINAL_B/VoiceSignature DSP and no browser access to Fish credentials.

Certification remains open until:

1. full CI passes;
2. Northflank deploy serves progressive bytes without proxy buffering;
3. physical Safari confirms playback starts before full synthesis completion;
4. keyboard and STT turns both speak reliably;
5. long replies no longer inherit the old multi-WAV gaps;
6. stop/barge-in and ACK remain correct;
7. expressive tags are audited physically for identity/naturalness;
8. background/screen-lock behavior is measured separately.

This file is a narrow V1.0.1 checkpoint. `docs/roadmap/VERSIONS.md` remains the lineage authority and should absorb this status at the next roadmap consolidation.

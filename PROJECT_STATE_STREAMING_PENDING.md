# PROJECT STATE — Fish streaming checkpoint

Data: 2026-10-30  
Branch: `feat/web-fish-turn-streaming`  
Decision: ADR-0022  
Status: implementation complete enough for CI; physical Safari certification pending.

This temporary checkpoint exists alongside `PROJECT_STATE.md` while the branch is under review. On merge/consolidation its facts should be folded into the canonical operational snapshot.

Current change:

- `WEB_SAFARI` no longer maps each short cognitive `SpeechSegment` to a separate Fish TTS call;
- one cognitive turn becomes one Web voice delivery while all internal segments remain persisted;
- Fish cloud emits PCM s16le incrementally through the Core and Safari plays it with `PcmStreamPlayer` before synthesis is complete;
- browser buffering is bounded and applies backpressure;
- Fish→Gemini fallback is allowed only before the first Fish PCM;
- Core emotion/cues may map to bounded Fish expressive tags; Fish does not own emotional state;
- cloud DSP remains `none`; FINAL_B/VoiceSignature stay preserved for explicit local paths;
- API keys/reference identifiers remain server-side.

Open gates:

1. PR CI;
2. Northflank build/deploy;
3. physical Safari progressive playback;
4. keyboard and STT reliability;
5. long-answer gap elimination;
6. underruns/stop/barge-in/ACK;
7. expressive-tag acoustic quality;
8. Safari/iOS background and screen-lock behavior.

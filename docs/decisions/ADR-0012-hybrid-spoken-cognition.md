# ADR-0012 — Hybrid Spoken Cognition: Surface Actor + Reasoning Specialist

- Status: Accepted for V1 candidate / physical and conversational certification pending
- Date: 2026-10-20
- Owners: BK + AMORA IA
- Scope: cognition provider orchestration, spoken realization, Android conversational latency

## Context

The first Android physical auditions showed that voice quality alone is not enough for Amora to feel conversational. BK observed four coupled problems:

1. long gaps between neural speech segments;
2. barge-in that was too hard or impossible while speech was being prepared;
3. microphone/VAD settings that rejected normal short speech too often;
4. language that could still sound like a digital assistant or chat response even when the acoustic voice was correct.

The existing production cognition path also asked one large reasoning-oriented model to perform the final social wording. That made a replaceable provider too visible in Amora's social surface and paid unnecessary reasoning latency for ordinary conversation.

Amora's canonical architecture already establishes that provider/model is capacity, not identity. Core owns identity, state, relationships, emotion, memory, intentions and social decisions.

## Decision

Use a hybrid model topology for remote dialogue realization:

```text
Amora Core (authoritative identity/state/DialoguePlan)
        |
        v
local complexity gate
        |
        +-- ordinary social / relational turn -----------------------+
        |                                                            |
        |                                                            v
        +-- complex factual / technical turn --> reasoning specialist |
                                              (private support note)  |
                                                       |              |
                                                       +--------------+
                                                                      v
                                                    spoken surface actor
                                                                      |
                                                                      v
                                                   local RealizationValidator
                                                                      |
                                                                      v
                                                     SpeechNormalizer
                                                                      |
                                                                      v
                                                    Android voice stack
```

### Spoken surface actor

The default Groq dialogue surface candidate is `qwen/qwen3.8-27b` in instruct/non-reasoning mode (`reasoning_effort=none`). Its task is to realize Amora's already-authorized DialoguePlan as natural Brazilian spoken language in first person.

It is not the owner of Amora's identity and may not invent canonical memory, relationship state, emotion, self facts or social objectives.

### Reasoning specialist

`openai/gpt-oss-120b` remains available as an internal reasoning specialist for turns selected by a deterministic local complexity gate. The specialist:

- does not role-play Amora;
- does not produce the final spoken reply;
- returns a short structured support note;
- is invoked only for eligible complex/technical/factual turns;
- uses bounded reasoning effort (`low`, or `medium` for deep turns);
- fails open to the surface actor: specialist failure must not mute Amora.

The support note is ephemeral and noncanonical. It is not persisted as truth simply because a model produced it.

### Spoken realization

The final language contract is explicitly speech-first:

- output is written to be heard, not formatted as chat;
- examples are verbalized (for example, `Por exemplo...`);
- options/sequences use oral transitions instead of bullets/tables;
- ordinary responses prefer short conversational turns rather than lectures;
- the provider writes Amora in first person, while Core remains the authority over what she is allowed to mean.

A deterministic `SpeechNormalizer` remains as a last-mile guard against visual chat syntax.

## Android delivery decision

The same candidate also changes local delivery so that:

- barge-in is active during neural preparation as well as playback;
- Supertonic generation is cancellable through the sherpa-onnx generation callback;
- speech VAD is less hostile to short commands such as `para`, `não` and `Amora`;
- speech segments are bounded smaller for the current whole-segment WORLD pipeline;
- segment N+1 may be pre-rendered while segment N is playing;
- claim/reservation and actual playback start are distinct states in the Core journal.

This is intended to reduce both first-response latency and the audible silence between consecutive segments without removing FINAL_B, VoiceSignature V1 Soft or Emotion/Prosody V2.

## Invariants

1. `provider != identity` remains absolute.
2. Core owns DialoguePlan and canonical state.
3. The reasoning specialist never speaks directly.
4. A specialist response is evidence/support, not canonical fact.
5. Final provider output must pass the local RealizationValidator before speech.
6. Barge-in cancels the current audio/preparation and the remaining cognitive turn.
7. FINAL_B morphology and VoiceSignature V1 Soft are not retuned to hide language/runtime problems.
8. CI success is not Galaxy A15 physical certification.

## Why Qwen 3.8 27B as the first surface candidate

Groq's current documentation lists `qwen/qwen3.8-27b` with instruct/non-thinking mode for efficient general-purpose dialogue, JSON modes and multilingual generation. Groq's prior Qwen 3 guidance explicitly included role-playing/character development and multi-turn dialogue. This makes it a useful first candidate for Amora's spoken social surface while keeping the existing Groq transport and free-plan-compatible operating target.

This is a candidate selection, not an irreversible canonical model choice. The provider remains swappable and should later be compared through Cognitive Replay against other roleplay-oriented candidates when latency, cost, availability and policy fit are acceptable.

References:
- https://console.groq.com/docs/model/qwen/qwen3.8-27b
- https://console.groq.com/docs/api-reference
- https://console.groq.com/docs/rate-limits
- sherpa-onnx v1.13.8 Kotlin TTS callback API: `sherpa-onnx/kotlin-api/Tts.kt`

## Certification gates

Before promotion to `main`/canonical V1 behavior, BK must physically verify on Galaxy A15:

- normal-volume short speech is detected reliably;
- `para`/clear barge-in interrupts preparation and playback;
- a long story does not resume old queued segments after interruption;
- gaps between consecutive speech segments are materially reduced;
- ordinary social replies sound like Amora speaking, not like an assistant/chat transcript;
- complex technical questions can use the specialist without exposing it in Amora's wording;
- the voice identity remains recognizably the approved FINAL_B + VoiceSignature stack.

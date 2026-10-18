# VoiceSignature DSP research — 2026-10-18

**Status:** EXPERIMENTAL / research note  
**Canonical authority:** `docs/canon/VOICE_IDENTITY.md` + ADR-0009  
**Purpose:** choose the next Voice Kitchen method after playback-rate pitch produced a chipmunk/alien artefact.

## Problem observed physically

BK reported that the current high-pitch DSP sounded like an ordinary voice being artificially raised: Alvin/chipmunk/alien-like, with a rough/hoarse quality. Increasing pitch further made the problem worse.

The 60% F1 / 40% F5 Supertonic style mix sounded better than the previous bases, so this experiment freezes that mix as the audition source instead of continuing speaker interpolation indefinitely.

## Candidates researched

### WORLD vocoder

Official project: <https://github.com/mmorise/World>

Confirmed properties:

- speech analysis/resynthesis into F0, spectral envelope and aperiodicity;
- modified BSD license;
- explicitly designed for high-quality speech manipulation and real-time applications.

Strength: very strong research/reference tool for independent speech-parameter manipulation.

Concern for Amora V1: integration is a larger monophonic vocoder pipeline and would need a separate Android/mobile implementation path plus later Web/PWA work.

### Signalsmith Stretch

Official project: <https://github.com/Signalsmith-Audio/signalsmith-stretch>

Pinned audition commit:

```text
a670068d9aeb64913331d5cc29337b19a457a7df
```

Confirmed properties:

- C++11 pitch/time processor;
- independent pitch shift and formant shift/compensation;
- explicit streaming/latency API (`inputLatency`, `outputLatency`);
- configurable block size / overlap;
- MIT license;
- official Web Audio/WASM release exists;
- upstream notes that its formant correction is less sharp than specialised monophonic algorithms such as PSOLA.

Strength for Amora: directly addresses the current chipmunk failure while remaining small, local, streamable and portable toward the planned Web/PWA client.

### Rubber Band

Official project: <https://github.com/breakfastquay/rubberband>

Confirmed properties:

- mature time/pitch processing;
- real-time use exists;
- GPL unless a separate commercial licence is obtained.

Decision: not preferred for the V1 default because licensing is a worse fit for a zero-cost, multi-platform project, especially with the planned Apple/PWA direction.

## Experimental decision

For the next audition, **Signalsmith Stretch is the production-shaped candidate**.

This is not yet a canonical promotion. The experiment must first prove by ear that formant manipulation improves the 60/40 source without producing chipmunk artefacts.

The first candidate set intentionally keeps pitch near zero:

| ID | pitch | formant |
|---|---:|---:|
| `SS_FORMANT_1P5_PITCH_0` | 0.0 st | +1.5 st |
| `SS_FORMANT_2P5_PITCH_0` | 0.0 st | +2.5 st |
| `SS_FORMANT_2P5_PITCH_0P5` | +0.5 st | +2.5 st |
| `SS_FORMANT_3P5_PITCH_0P5` | +0.5 st | +3.5 st |

All use automatic formant-base estimation and the upstream default-style 120 ms block / 4x overlap for the quality-first audition.

## Latency gate

The Voice Kitchen records two different measurements:

1. **algorithmic latency** reported by Signalsmith for the configured block/overlap;
2. **processing RTF** measured on the GitHub x86_64 runner.

Neither is Galaxy A15 certification.

If the sound wins the A/B test, the next step is an Android-native benchmark using the same pinned implementation, measuring first-utterance latency, streaming latency, RTF, CPU, RAM, temperature and barge-in impact on the Galaxy A15 before promotion.

## Vocal-synth texture

This pass deliberately does **not** add Vocaloid-like texture. The pipeline is split:

```text
Supertonic 60/40 base
        ↓
formant / very small pitch shaping
        ↓
clean stable voice
        ↓
future vocal-synth texture layer
```

This prevents us from debugging vocal identity and synthetic texture at the same time.

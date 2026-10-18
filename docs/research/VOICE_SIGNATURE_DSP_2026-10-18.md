# VoiceSignature DSP research — 2026-10-18

**Status:** EXPERIMENTAL / SIGNALSMITH AUDITION REJECTED  
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

Strength: strong research/reference tool for independent speech-parameter manipulation.

Concern for Amora V1: integration is a larger monophonic vocoder pipeline and still needs separate Android/mobile measurement plus later Web/PWA work.

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

The quality-first audition used 120 ms block / 4x overlap and measured 120 ms algorithmic latency for that configuration. Processing on the GitHub x86_64 runner was fast (mean RTF about 0.015), but that number does not certify Galaxy A15.

### Rubber Band

Official project: <https://github.com/breakfastquay/rubberband>

Confirmed properties:

- mature time/pitch processing;
- real-time use exists;
- GPL unless a separate commercial licence is obtained.

Decision: not preferred for the V1 default because licensing is a worse fit for a zero-cost, multi-platform project, especially with the planned Apple/PWA direction.

## Signalsmith audition result

The Voice Kitchen generated these candidates from the same Supertonic 60/40 base:

| ID | pitch | formant |
|---|---:|---:|
| `SS_FORMANT_1P5_PITCH_0` | 0.0 st | +1.5 st |
| `SS_FORMANT_2P5_PITCH_0` | 0.0 st | +2.5 st |
| `SS_FORMANT_2P5_PITCH_0P5` | +0.5 st | +2.5 st |
| `SS_FORMANT_3P5_PITCH_0P5` | +0.5 st | +3.5 st |

Physical listening result from BK:

```text
chipmunk/esquilo: improved
alien timbre: failed
```

Therefore uniform formant shifting with this Signalsmith setup is **rejected as the next VoiceSignature path**. It proved that separating pitch from formant solves one failure mode, but it did not preserve a believable vocal-tract identity.

## New direction

The next audition moves to WORLD and uses substantially smaller transformations:

```text
Supertonic 60/40 base
        ↓
WORLD analysis
├─ F0
├─ spectral envelope
└─ aperiodicity
        ↓
very small, independent transforms
        ↓
WORLD resynthesis
```

A neutral WORLD resynthesis is included so BK can determine whether any alien quality comes from the vocoder itself before judging the transforms.

The first WORLD set keeps:

- F0 at 0 to +0.35 semitone;
- vocal-tract spectral warp at 1.000x to 1.055x (about 0 to +0.93 semitone equivalent);
- aperiodicity at 1.00x to 0.96x.

This is intentionally much more conservative than the rejected +1.5 to +3.5 semitone uniform formant pass.

## Vocal-synth texture

The experiment still deliberately does **not** add Vocaloid-like texture yet. The pipeline remains split:

```text
Supertonic 60/40 base
        ↓
clean source/filter shaping
        ↓
clean stable voice
        ↓
future vocal-synth texture layer
```

If WORLD also produces an alien identity even in neutral resynthesis or tiny transforms, stop classical DSP escalation and evaluate a neural voice-conversion / different voice-generation backend instead of stacking more filters.

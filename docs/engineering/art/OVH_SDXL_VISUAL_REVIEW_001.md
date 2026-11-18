# Amora Art — OVH SDXL live visual audit 001

**Status:** REJECTED_FOR_DATASET — provider transport confirmed, visual acceptance failed.
**Date:** 2026-11-17 (Brazil local) / 2026-11-18 (UTC).
**Environment:** PR #286 isolated GitHub Actions runner; no Amora Core boot, no database or production deployment.

## Reproduction

- Run: https://github.com/Sllay/Amora-Chibi/actions/runs/44206583273
- Artifact: `amora-ovh-live-probe-44206583273` with `ovh_sdxl_live.png`.
- Provider: `ovh-sdxl`; anonymous inference.
- SHA-256 of output: `7dcfa77e1d66b21254489a28166a244643b062ae1fe447d5847f40eab3d5589a`.
- Reported elapsed: 18.69 seconds; 1,617,416 bytes; `image/png`.
- Input target: an original tiny, friendly **woodland creature**, colored hand-drawn cartoon, leaf-shaped ears, full figure.
- Output observation: monochromatic line-art **human girl** in woodland, holding a book.

## Visual QA (human review)

- **Subject adherence: FAIL.** A human figure instead of the requested woodland creature.
- **Anatomy: FAIL.** A face-like eye mark on the forehead; face details ambiguous/duplicated; fingers and book fused; weakly defined or distorted shoes/feet.
- **Finish: FAIL.** Monochrome sketch rather than desired pastel color illustration.
- **Composition: MIXED.** Subject fairly visible but intricate background distracts, reducing silhouette clarity.
- **Identity consistency: NOT TESTED.** The canonical Amora multi-view sheets were not included.
- **Transport and file integrity: PASS.** HTTP response and recognizable PNG, but this is not a visual-quality certification.

This image is **quarantined from LoRA training**. It must not be automatically promoted as a canonical reference.

## Likely causes and next discriminating experiments

- The endpoint uses the **SDXL base** model, with no selected cartoon fine-tune/LoRA; reliable hand anatomy and prompt fidelity are not guaranteed.
- The authored `STYLE` prompt combines rich style instructions with a longer user request. Check whether the model prioritizes generic human cartoon content in tests.
- Compare one short, subject-specific prompt vs. a simplified style constraint, in the *same* endpoint, before attributing the output to one cause. Do not repeatedly call a free endpoint without bounded tests.
- Compare against a backend that truly supports references, LoRA or image-to-image before choosing the model for canonical Amora art.
- Do **not** attempt to solve the foundational reference-control gap through increasingly long negative prompts alone.

## Acceptance gate

`transport_pass` and `visual_pass` are separate fields. Generated art is NOT training-ready unless manually reviewed, correctly attributed/licensed and explicitly approved. In V1 the dataset CLI already requires `approved=true`, which must only be set *after* that review.

No merge/deploy to main while the Aiven PostgreSQL migration is active.

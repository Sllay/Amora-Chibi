# Amora-bolinha — Ayatsuri2D GitHub Actions prototype

**Status: EXPERIMENTAL / STATIC EXPORT ONLY.**

This is an isolated pipeline for building a Cubism-compatible static `.moc3` bundle
using the real MIT-licensed `ayatsuri-import` and `ayatsuri-format` Rust crates
from Ayatsuri2D, pinned to commit `080f72b63a37098ce0f4974699871230beeecb65`.

It is not a streaming-ready animated model yet. Declaring parameter IDs is NOT
equivalent to implementing a blink, mouth rig, tracking, squishy deformation or physics.
Do not present a successful static export as a verified Live2D rig.

## Approved appearance
- Front-facing, round white fluffy Amora with two horns included in the body.
- No hair, no paws; paw poses will be added by the artist later.
- Two eyes, each with **four independently controllable art pieces**: white,
  ruby iris with integrated highlights, upper lid and distinct lower lid.
- One isolated mouth. Light gradients only, base palette max 16 colors.
- Transparent RGBA layers on one common 1024 × 1024 canvas, same coordinates.
- Manifest order is back-to-front. No flattened eye artwork in the body layer.

## Mobile-first workflow
1. From the phone, upload the **10 actual separated transparent PNG** layers
   to the `layers/` paths in `manifest.json`. The currently generated single
   preview PNG is not sufficient and must not be represented as layered art.
2. GitHub Actions runs Rust unit tests, then exports `AmoraBolinha.moc3`,
   `AmoraBolinha.model3.json`, a display-info JSON and textures.
3. Download the GitHub Actions artifact to the phone and inspect it.
4. **Remaining work:** construct meshes, keyforms and clipping; implement
   real eyelid blink, eye-bounce (poing), mouth movement and squishy body
   physics; re-export and verify inside VTube Studio on the phone.

The command for testing or exporting locally on a Linux/Windows/macOS runner is:
```
cargo test --manifest-path tools/ayatsuri-batch/Cargo.toml --all-targets
cargo run --release --manifest-path tools/ayatsuri-batch/Cargo.toml -- \
  assets/amora-live2d/manifest.json artifacts/AmoraBolinha
```
The second command intentionally fails until every named PNG exists, rather
than inventing missing layers.

## Implementation scope and limitations
- No GUI, remote desktop or display server required for this static exporter.
- Uses Ayatsuri2D's `from_layers` and `to_moc3` functions directly.
- Model version pinned to **moc3 5.0**, not 5.3, for VTube Studio support.
- Invalid paths, dimensions, blank layers and missing assets fail the job.
- Existing Amora Core, web avatar studio, runtime and deploy pipelines untouched.

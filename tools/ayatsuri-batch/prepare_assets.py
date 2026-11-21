#!/usr/bin/env python3
"""Validate approved raw Amora artwork and prepare an Ayatsuri2D static proof.

Never modify the uploaded 10 source files. Only derive a PNG from the opaque
JPEG body for import, and sort drawing order for rendering: JPEG must be drawn
behind the whites and irises or it covers them. The 1..10 SOURCE ORDER remains
unaltered and recorded in manifest.json.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from PIL import Image


def prepare(manifest_path: Path, out: Path) -> None:
    src = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (src["width"], src["height"]) != (1254, 1254):
        raise ValueError("Expected approved 1254x1254 Amora canvas")
    expected_names = [
        "eye_white_L", "iris_L", "eye_white_R", "iris_R", "body_horns",
        "lower_lid_L", "lower_lid_R", "upper_lid_R", "upper_lid_L", "mouth"
    ]
    if [v["name"] for v in src["layers"]] != expected_names:
        raise ValueError("Source layer order changed, will not silently reorder inputs")
    out.mkdir(parents=True, exist_ok=True)
    (out / "layers").mkdir(parents=True, exist_ok=True)
    rgba = {}
    for i, spec in enumerate(src["layers"]):
        rel = Path(spec["file"])
        if rel.is_absolute() or ".." in rel.parts or len(rel.parts) != 2 or rel.parts[0] != "layers":
            raise ValueError(f"unsafe layer path: {rel}")
        inp = manifest_path.parent / rel
        raw = inp.read_bytes()
        if hashlib.sha256(raw).hexdigest() != spec["sha256"]:
            raise ValueError(f"{rel}: supplied art differs from approved original")
        with Image.open(inp) as image:
            image.load()
            if image.size != (1254, 1254):
                raise ValueError(f"{rel}: incorrect canvas")
            if spec["name"] == "body_horns":
                if image.format != "JPEG":
                    raise ValueError("body/horns must remain the original JPEG source")
            elif image.format != "PNG" or image.mode != "RGBA":
                raise ValueError(f"{rel}: expected original transparent RGBA PNG")
            rgba[spec["name"]] = image.convert("RGBA")

    # Sorting is limited to the exported VISUAL composition, not the source order.
    ordered = [src["layers"][4], *src["layers"][0:4], *src["layers"][5:]]
    prepared_layers = []
    preview = Image.new("RGBA", (1254, 1254), (0, 0, 0, 0))
    for spec in ordered:
        orig = manifest_path.parent / spec["file"]
        filename = Path(spec["file"]).stem + ".png"
        target = out / "layers" / filename
        if spec["name"] == "body_horns":
            rgba[spec["name"]].save(target)
        else:
            shutil.copyfile(orig, target)  # exact unchanged original PNG bytes
        preview.alpha_composite(rgba[spec["name"]])
        prepared_layers.append({
            "name": spec["name"], "group": spec["group"], "file": f"layers/{filename}"
        })

    preview.save(out / "amora_montada.png")
    (out / "manifest.json").write_text(
        json.dumps({"name": src["name"], "width": 1254, "height": 1254,
                    "layers": prepared_layers}, indent=2) + "\n", encoding="utf-8")
    (out / "source_order.json").write_text(
        json.dumps({"source_order": expected_names, "render_order":
                    [v["name"] for v in ordered], "warning":
                    "Body source is opaque JPEG. Preview places body behind eyes; source files untouched."},
                   indent=2) + "\n", encoding="utf-8")
    print("Validated 10 source layers. Only eye whites #1 and #3 replaced.")
    print("Static export will use a technical PNG copy of JPEG body; user source preserved.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: python prepare_assets.py <source-manifest> <prepared-output-dir>")
    prepare(Path(sys.argv[1]), Path(sys.argv[2]))

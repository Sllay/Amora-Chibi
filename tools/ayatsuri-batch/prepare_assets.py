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
from PIL import Image, ImageDraw, ImageFilter



def face_patch(body: Image.Image, white: Image.Image, lower: Image.Image, upper: Image.Image) -> Image.Image:
    """Feathered patch sampled ONLY from original body JPEG; no painted pixels."""
    bounds = [im.getchannel("A").getbbox() for im in (white, lower, upper)]
    bounds = [b for b in bounds if b is not None]
    if len(bounds) != 3:
        raise ValueError("eye part is empty; cannot derive skin patch")
    x0 = min(b[0] for b in bounds)
    y0 = min(b[1] for b in bounds)
    x1 = max(b[2] for b in bounds)
    y1 = max(b[3] for b in bounds)
    width, height = x1-x0, y1-y0
    pad_x, pad_y = max(10, round(width*.18)), max(10, round(height*.23))
    x0, y0 = max(0,x0-pad_x), max(0,y0-pad_y)
    x1, y1 = min(body.width,x1+pad_x), min(body.height,y1+pad_y)
    w,h = x1-x0,y1-y0
    # Feathered ellipse: its outermost pixels blend into the unchanged JPEG.
    mask = Image.new("L", (w,h),0)
    ImageDraw.Draw(mask).ellipse((round(w*.075),round(h*.075),
                                  round(w*.925),round(h*.925)), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=max(3,round(min(w,h)*.07))))
    crop = body.crop((x0,y0,x1,y1)).copy()
    crop.putalpha(mask)
    result = Image.new("RGBA",body.size,(0,0,0,0))
    result.alpha_composite(crop,(x0,y0))
    return result


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

    # Source list remains ten items. Derived skin pieces render immediately
    # after the original body; the user's images / hashes remain untouched.
    ordered = [src["layers"][4], *src["layers"][0:4], *src["layers"][5:]]
    skin = {
        side: face_patch(rgba["body_horns"], rgba[f"eye_white_{side}"],
                         rgba[f"lower_lid_{side}"], rgba[f"upper_lid_{side}"])
        for side in ("L", "R")
    }
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
        if spec["name"] == "body_horns":
            for side in ("L","R"):
                technical = f"skin_patch_{side}"
                technical_file = f"layers/{technical}.png"
                skin[side].save(out / technical_file)
                preview.alpha_composite(skin[side])
                prepared_layers.append({"name":technical,"group":"Face", "file":technical_file})

    preview.save(out / "amora_montada.png")
    (out / "manifest.json").write_text(
        json.dumps({"name": src["name"], "width": 1254, "height": 1254,
                    "layers": prepared_layers}, indent=2) + "\n", encoding="utf-8")
    (out / "source_order.json").write_text(
        json.dumps({"source_order": expected_names, "render_order":
                    ["body_horns", "skin_patch_L", "skin_patch_R",
                     *[v["name"] for v in ordered if v["name"] != "body_horns"]],
                    "technical_derived_layers": ["skin_patch_L","skin_patch_R"], "warning":
                    "Body source is opaque JPEG. Preview places body behind eyes; source files untouched."},
                   indent=2) + "\n", encoding="utf-8")
    print("Validated 10 source layers. Only eye whites #1 and #3 replaced.")
    print("Prepared 12 art meshes: ten untouched inputs plus two technical feathered skin patches.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: python prepare_assets.py <source-manifest> <prepared-output-dir>")
    prepare(Path(sys.argv[1]), Path(sys.argv[2]))

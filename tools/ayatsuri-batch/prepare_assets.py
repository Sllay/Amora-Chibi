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
from collections import deque
import shutil
import sys
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFilter




def fill_eye_hole(body: Image.Image, white: Image.Image) -> Image.Image:
    """Repair the original JPEG's pure-white eye socket behind an eyelid.

    The underlying artwork deliberately has white eye holes. They MUST be
    covered when the animated eye mesh becomes fully closed. Reconstruct just
    this technical hidden region from skin pixels directly above/below it;
    never change the source JPEG or user-supplied eye art.
    """
    bounds = white.getchannel("A").getbbox()
    if bounds is None:
        raise ValueError("eye white has no painted area")
    x0,y0,x1,y1 = bounds
    cx,cy = (x0+x1)//2,(y0+y1)//2
    pix = body.load()
    def pure_white(x: int,y: int) -> bool:
        r,g,b = pix[x,y][:3]
        return min(r,g,b) >= 251 and max(r,g,b)-min(r,g,b) <= 2

    seed = next(((x,y) for radius in range(0,35,4)
                 for y in range(max(y0,cy-radius),min(y1,cy+radius+1),max(1,radius//2 or 1))
                 for x in range(max(x0,cx-radius),min(x1,cx+radius+1),max(1,radius//2 or 1))
                 if pure_white(x,y)),None)
    if seed is None:
        raise ValueError("body eye hole not found: refuse invisible blink")
    q = deque([seed])
    seen = {seed}
    mask = Image.new("L",body.size,0)
    px_mask = mask.load()
    while q:
        x,y = q.popleft()
        if not pure_white(x,y):
            continue
        px_mask[x,y] = 255
        for point in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
            nx,ny=point
            if x0 <= nx < x1 and y0 <= ny < y1 and point not in seen:
                seen.add(point)
                q.append(point)
        if len(seen) > (x1-x0)*(y1-y0)*.75:
            raise ValueError("eye hole flood filled too much of the face")
    filled_bounds = mask.getbbox()
    if filled_bounds is None:
        raise ValueError("eye hole mask unexpectedly empty")
    bx0,by0,bx1,by1=filled_bounds
    if (bx1-bx0)*(by1-by0) < 250:
        raise ValueError("eye hole detected area is suspiciously small")
    broad = mask.filter(ImageFilter.MaxFilter(19))
    bb=broad.getbbox()
    if bb is None:
        raise ValueError("eye socket expansion failed")
    output = body.copy()
    edited = output.load()
    broad_pix = broad.load()
    def skin_sample(x: int,y: int) -> tuple[int,int,int,int]:
        # Average neighboring JPEG pixels to avoid vertical sampling stripes.
        samples=[]
        for dx in (-6,-3,0,3,6):
            for dy in (-2,0,2):
                xx=min(body.width-1,max(0,x+dx))
                yy=min(body.height-1,max(0,y+dy))
                samples.append(pix[xx,yy])
        return tuple(round(sum(c[i] for c in samples)/len(samples)) for i in range(4))
    for x in range(bb[0],bb[2]):
        ys=[y for y in range(bb[1],bb[3]) if broad_pix[x,y]]
        if not ys:
            continue
        top,bottom=ys[0],ys[-1]
        a=skin_sample(x,max(0,top-11))
        b=skin_sample(x,min(body.height-1,bottom+11))
        for y in ys:
            ratio=(y-top)/max(1,bottom-top)
            # smoothstep avoids a visible seam against the top/bottom skin.
            t=ratio*ratio*(3-2*ratio)
            edited[x,y]=tuple(round((1-t)*a[i]+t*b[i]) for i in range(4))
    # Blend only the reconstructed eye hole into the unchanged source.
    feather=broad.filter(ImageFilter.GaussianBlur(4))
    return Image.composite(output,body,feather)



def remove_border_background(image: Image.Image) -> Image.Image:
    """Cut near-white canvas background only if connected to the border."""
    rgb = image.convert("RGB")
    red, green, blue = rgb.split()
    cutoff = lambda channel: channel.point(lambda p: 255 if p >= 244 else 0)
    near_white = ImageChops.multiply(
        ImageChops.multiply(cutoff(red), cutoff(green)), cutoff(blue)
    )
    if near_white.getpixel((0, 0)) != 255:
        raise ValueError("Expected white JPEG background at image corner")
    ImageDraw.floodfill(near_white, (0, 0), 128, thresh=0)
    alpha = near_white.point(lambda p: 0 if p == 128 else 255)
    alpha = alpha.filter(ImageFilter.MinFilter(3))
    alpha = alpha.filter(ImageFilter.GaussianBlur(radius=0.45))
    output = image.convert("RGBA")
    output.putalpha(alpha)
    return output


def prepared_body(body: Image.Image, eye_white_l: Image.Image, eye_white_r: Image.Image) -> Image.Image:
    """Replace white holes with skin, then remove the white JPEG background."""
    repaired = fill_eye_hole(body, eye_white_l)
    repaired = fill_eye_hole(repaired, eye_white_r)
    return remove_border_background(repaired)


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
    # The derived body was already repaired before generating the skin mesh.
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
    clean_body = prepared_body(rgba["body_horns"], rgba["eye_white_L"], rgba["eye_white_R"])
    skin = {
        side: face_patch(clean_body, rgba[f"eye_white_{side}"],
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
            clean_body.save(target)
        else:
            shutil.copyfile(orig, target)  # exact unchanged original PNG bytes
        preview.alpha_composite(clean_body if spec["name"] == "body_horns" else rgba[spec["name"]])
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
    print("Prepared 12 art meshes: 10 untouched inputs plus 2 technical skin patches covering JPEG white eye holes.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: python prepare_assets.py <source-manifest> <prepared-output-dir>")
    prepare(Path(sys.argv[1]), Path(sys.argv[2]))

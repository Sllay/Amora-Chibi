#!/usr/bin/env python3
"""Build a single-file, offline/mobile facial-rig TEST preview from approved art.

Never changes source layers. The HTML visualizer models the parameter behavior
but does not replace running the actual MOC3 in a Cubism-compatible renderer.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
from pathlib import Path

from PIL import Image
from prepare_assets import face_patch, prepared_body, load_extra_assets, load_user_closed_eyes

def build(manifest: Path, output: Path) -> None:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    layers = {}
    rgba = {}
    for layer in data["layers"]:
        source = manifest.parent / layer["file"]
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != layer["sha256"]:
            raise ValueError(f"Original source hash changed: {source}")
        with Image.open(source) as im:
            if im.size != (data["width"], data["height"]):
                raise ValueError(f"Wrong canvas size: {source}")
            pixels = im.convert("RGBA")
            rgba[layer["name"]] = pixels
            a = pixels.getchannel("A")
            box = a.getbbox()
            if box is None:
                raise ValueError(f"Empty alpha channel: {source}")
            layers[layer["name"]] = {
                "src": "data:image/" + ("jpeg" if source.suffix.lower() in (".jpeg", ".jpg") else "png") +
                    ";base64," + base64.b64encode(raw).decode("ascii"),
                "box": list(box),
            }
    body = prepared_body(rgba["body_horns"], rgba["eye_white_L"], rgba["eye_white_R"])
    body_binary = io.BytesIO()
    body.save(body_binary, format="PNG")
    layers["body_horns"]["src"] = "data:image/png;base64,"+base64.b64encode(body_binary.getvalue()).decode("ascii")
    layers["body_horns"]["box"] = list(body.getchannel("A").getbbox())
    for side in ("L","R"):
        generated = face_patch(body,rgba[f"eye_white_{side}"],
                               rgba[f"lower_lid_{side}"],rgba[f"upper_lid_{side}"])
        output_bytes = io.BytesIO()
        generated.save(output_bytes,format="PNG")
        binary = output_bytes.getvalue()
        layers[f"skin_patch_{side}"] = {
            "src":"data:image/png;base64,"+base64.b64encode(binary).decode("ascii"),
            "box":list(generated.getchannel("A").getbbox()),
        }
        # Same procedurally derived closed pose as the actual MOC3 importer:
        # never use different artwork in the HTML demonstration.
        closure = load_user_closed_eyes(manifest)[side]
        closed_data=io.BytesIO()
        closure.save(closed_data,format="PNG")
        layers[f"closed_smile_{side}"] = {
            "src":"data:image/png;base64,"+base64.b64encode(closed_data.getvalue()).decode("ascii"),
            "box":list(closure.getchannel("A").getbbox()),
        }
    # Embed the exact same six extra art layers used in the .moc3 export,
    # keeping this self-contained HTML functional offline on iOS.
    for name,im in load_extra_assets(manifest).items():
        data_bytes=io.BytesIO()
        im.save(data_bytes,format="PNG",optimize=True)
        layers[name]={
            "src":"data:image/png;base64,"+base64.b64encode(data_bytes.getvalue()).decode("ascii"),
            "box":list(im.getchannel("A").getbbox()),
        }
    payload = json.dumps({"width": data["width"], "height": data["height"], "layers": layers},
                         separators=(",", ":"))
    template = (Path(__file__).parent / "mobile_preview_template.html").read_text(encoding="utf-8")
    if "__AMORA_PREVIEW_DATA__" not in template:
        raise ValueError("Preview template placeholder was not found")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(template.replace("__AMORA_PREVIEW_DATA__", payload), encoding="utf-8")
    print(f"Offline interactive rig preview: {output} ({output.stat().st_size} bytes)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: build_preview.py <source-manifest> <output-html>")
    build(Path(sys.argv[1]), Path(sys.argv[2]))

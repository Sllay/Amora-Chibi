#!/usr/bin/env python3
"""Build a single-file, offline/mobile facial-rig TEST preview from approved art.

Never changes source layers. The HTML visualizer models the parameter behavior
but does not replace running the actual MOC3 in a Cubism-compatible renderer.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image

def build(manifest: Path, output: Path) -> None:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    layers = {}
    for layer in data["layers"]:
        source = manifest.parent / layer["file"]
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != layer["sha256"]:
            raise ValueError(f"Original source hash changed: {source}")
        with Image.open(source) as im:
            if im.size != (data["width"], data["height"]):
                raise ValueError(f"Wrong canvas size: {source}")
            a = im.convert("RGBA").getchannel("A")
            box = a.getbbox()
            if box is None:
                raise ValueError(f"Empty alpha channel: {source}")
            layers[layer["name"]] = {
                "src": "data:image/" + ("jpeg" if source.suffix.lower() in (".jpeg", ".jpg") else "png") +
                    ";base64," + base64.b64encode(raw).decode("ascii"),
                "box": list(box),
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

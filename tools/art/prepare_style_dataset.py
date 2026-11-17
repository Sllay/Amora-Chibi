#!/usr/bin/env python3
"""Export only owner-approved images/captions as an auditable SDXL LoRA ZIP.

Usage: python tools/art/prepare_style_dataset.py manifest.json output.zip
No provider calls, training, hidden downloads or invented canonical identity.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

MAX_IMAGES = 250
MAX_BYTES = 8 * 1024 * 1024
VIEWS = {
    "front", "back", "left", "right", "top", "bottom",
    "three_quarter", "expression", "scene", "other",
}
MIMES = {".png": b"\x89PNG\r\n\x1a\n", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff", ".webp": b"RIFF"}


def prepare(manifest_path: Path, output_path: Path) -> dict:
    root = manifest_path.resolve().parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("images")
    if not isinstance(entries, list) or not entries or len(entries) > MAX_IMAGES:
        raise ValueError("images must contain 1..250 entries")
    prepared = []
    seen_hashes = set()
    for index, record in enumerate(entries):
        if not isinstance(record, dict) or record.get("approved") is not True:
            raise ValueError(f"entry {index}: manual approval required")
        if record.get("source") not in {"original", "curated_generation"}:
            raise ValueError(f"entry {index}: source must be explicit")
        if record.get("license") not in {"owned", "authorized"}:
            raise ValueError(f"entry {index}: license must be verified")
        if record.get("view") not in VIEWS:
            raise ValueError(f"entry {index}: view must be explicit")
        caption = record.get("caption")
        if not isinstance(caption, str) or not (8 <= len(caption.strip()) <= 500):
            raise ValueError(f"entry {index}: caption must be 8..500 characters")
        relative = record.get("path")
        if not isinstance(relative, str) or not relative:
            raise ValueError(f"entry {index}: missing path")
        path = (root / relative).resolve()
        if path == root or root not in path.parents or not path.is_file():
            raise ValueError(f"entry {index}: image path outside dataset")
        if path.suffix.lower() not in MIMES:
            raise ValueError(f"entry {index}: image format unsupported")
        size = path.stat().st_size
        if not (32 <= size <= MAX_BYTES):
            raise ValueError(f"entry {index}: image size invalid")
        data = path.read_bytes()
        if not data.startswith(MIMES[path.suffix.lower()]):
            raise ValueError(f"entry {index}: invalid image signature")
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen_hashes:
            raise ValueError(f"entry {index}: duplicate image")
        seen_hashes.add(digest)
        prepared.append((record, path.suffix.lower().replace(".jpeg", ".jpg"), data, digest))

    # SHA-based split is deterministic and independent of manifest order.
    validation = {
        digest for digest in sorted(item[3] for item in prepared)[::5][:max(1, len(prepared) // 5)]
    } if len(prepared) >= 10 else set()
    meta = {"version": 1, "base_model": "stabilityai/stable-diffusion-xl-base-1.0", "images": []}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output_path, "w", compression=ZIP_DEFLATED) as archive:
        for index, (record, ext, data, digest) in enumerate(prepared):
            split = "val" if digest in validation else "train"
            basename = f"{index:03}"
            archive.writestr(f"{split}/{basename}{ext}", data)
            archive.writestr(f"{split}/{basename}.txt", record["caption"].strip() + "\n")
            meta["images"].append({
                "id": basename, "split": split, "sha256": digest,
                "view": record["view"], "source": record["source"],
                "license": record["license"],
            })
        archive.writestr("dataset_metadata.json", json.dumps(meta, indent=2, ensure_ascii=False))
    return {"images": len(prepared), "train": len(prepared) - len(validation), "val": len(validation)}


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: prepare_style_dataset.py manifest.json output.zip", file=sys.stderr)
        return 2
    try:
        print(json.dumps(prepare(Path(sys.argv[1]), Path(sys.argv[2])), ensure_ascii=False))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"dataset rejected: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

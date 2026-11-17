from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from tools.art.prepare_style_dataset import prepare


def _manifest(tmp_path: Path, image_path: str = "front.png", *, approved=True):
    data = b"\x89PNG\r\n\x1a\n" + b"\0" * 50
    (tmp_path / "front.png").write_bytes(data)
    body = {"images": [{
        "path": image_path, "caption": "amora_original, hand drawn front view",
        "view": "front", "source": "original", "license": "owned",
        "approved": approved,
    }]}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(body))
    return path


def test_approved_dataset_is_zip_with_caption_and_hash(tmp_path):
    output = tmp_path / "dataset.zip"
    result = prepare(_manifest(tmp_path), output)
    assert result == {"images": 1, "train": 1, "val": 0}
    with ZipFile(output) as archive:
        assert "train/000.png" in archive.namelist()
        assert "train/000.txt" in archive.namelist()
        assert len(json.loads(archive.read("dataset_metadata.json"))["images"][0]["sha256"]) == 64


def test_rejects_unapproved_or_path_escape(tmp_path):
    with pytest.raises(ValueError):
        prepare(_manifest(tmp_path, approved=False), tmp_path / "x.zip")
    with pytest.raises(ValueError):
        prepare(_manifest(tmp_path, image_path="../secret.png"), tmp_path / "x.zip")

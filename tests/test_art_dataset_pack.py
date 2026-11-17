from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile


CLI = Path(__file__).resolve().parents[1] / "tools/art/prepare_style_dataset.py"


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


def _run(manifest: Path, output: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI), str(manifest), str(output)],
        capture_output=True, text=True, check=False, timeout=10,
    )


def test_approved_dataset_is_zip_with_caption_and_hash(tmp_path):
    output = tmp_path / "dataset.zip"
    completed = _run(_manifest(tmp_path), output)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"images": 1, "train": 1, "val": 0}
    with ZipFile(output) as archive:
        assert "train/000.png" in archive.namelist()
        assert "train/000.txt" in archive.namelist()
        assert len(json.loads(archive.read("dataset_metadata.json"))["images"][0]["sha256"]) == 64


def test_rejects_unapproved_or_path_escape(tmp_path):
    unapproved = _run(_manifest(tmp_path, approved=False), tmp_path / "x.zip")
    assert unapproved.returncode == 1
    assert not (tmp_path / "x.zip").exists()

    outside = _run(_manifest(tmp_path, image_path="../secret.png"), tmp_path / "x.zip")
    assert outside.returncode == 1
    assert not (tmp_path / "x.zip").exists()

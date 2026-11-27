"""Fail-closed integrity check for opt-in private V5 bundles (startup only)."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

CORE_SHA = "8741f739779b5d5210872bd3d7d99f0f1e56e6c87409e7d26d6bb4b80aa1ef47"
MOC_SHA = "dd23be7602621ea558b3bca17e2a23ebadb4b5d9b5a08eb73b96169b114e5cc0"
REQUIRED = {"identity.json", "index.html", "app.js", "presentation.js",
            "lab.css", "presentation.css", "live-overlay/index.html", "companion/index.html",
            "vendor/live2dcubismcore.min.js", "model/AmoraBolinha.model3.json"}

def verify_preview(root: Path):
    root = root.resolve()
    manifest = root / "manifest.json"
    if not manifest.is_file() or manifest.is_symlink() or manifest.stat().st_size > 256 * 1024:
        raise RuntimeError("V5 preview manifest missing or invalid")
    try:
        payload = json.loads(manifest.read_text())
        files = payload["files"]
        if payload["version"] != 1 or not isinstance(files, dict) or not REQUIRED <= files.keys() or len(files)>4096:
            raise ValueError()
        if files["vendor/live2dcubismcore.min.js"] != CORE_SHA or MOC_SHA not in files.values():
            raise ValueError()
        total = 0
        for name, digest in files.items():
            p = PurePosixPath(name)
            if p.is_absolute() or ".." in p.parts or str(p)!=name or not re.fullmatch("[a-f0-9]{64}", digest):
                raise ValueError()
            path = root.joinpath(*p.parts)
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError()
            total += path.stat().st_size
            if total > 128 * 1024 * 1024:
                raise ValueError()
            with path.open("rb") as stream:
                if hashlib.file_digest(stream,"sha256").hexdigest() != digest:
                    raise ValueError()
        actual = set()
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError()
            if path.is_file():
                actual.add(path.relative_to(root).as_posix())
        if actual != set(files) | {"manifest.json"}:
            raise ValueError()
        identity = json.loads((root/"identity.json").read_text())
        if identity["coreSha256"] != CORE_SHA or identity["mocSha256"] != MOC_SHA or not re.fullmatch("[a-f0-9]{40}",identity["build"]):
            raise ValueError()
    except (ValueError, KeyError, TypeError, OSError, UnicodeError):
        raise RuntimeError("V5 preview bundle integrity check failed") from None

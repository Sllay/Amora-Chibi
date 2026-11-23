"""Altare package images must be an actual deployable linux/amd64 manifest.

The GHCR versions with provenance attestations were displayed as two OS/Arch
platforms although only one real architecture was built. This test prevents
re-introducing a pseudo-platform in the Altare staging release.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_normal_and_sealed_workflows_build_plain_amd64_manifest():
    files = [
        ROOT / ".github/workflows/altare-package-images.yml",
        ROOT / ".github/workflows/altare-package-sealed-images.yml",
    ]
    for path in files:
        contents = path.read_text(encoding="utf-8")
        assert "platforms: linux/amd64" in contents, path.name
        assert "provenance: false" in contents, path.name
        assert "sbom: false" in contents, path.name
        assert "provenance: true" not in contents, path.name
        assert "ALTARE_MANIFEST_SINGLE_IMAGE_OK" in contents, path.name
        assert "ALTARE_MANIFEST_SINGLE_PLATFORM_OK" in contents, path.name
        assert "docker buildx imagetools inspect --raw" in contents, path.name
        assert "push: true" in contents, path.name

"""No silent skip for CPU wheel local versions, CVEs or missing audit coverage."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def audit_module():
    path = Path(__file__).parents[1] / "tools" / "security" / "audit-omnivoice.py"
    spec = importlib.util.spec_from_file_location("omnivoice_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_audit_public_versions_do_not_modify_cpu_install_artifacts(tmp_path):
    module = audit_module()
    lock = tmp_path / "requirements.lock"
    original = "\n".join(
        f"{name}==2.11.0+cpu --hash=sha256:" + "0" * 64
        for name in ("torch", "torchaudio")
    )
    lock.write_text(original)
    result = module.audit_requirements(lock)
    assert result == "torch==2.11.0\ntorchaudio==2.11.0\n"
    assert lock.read_text() == original


@pytest.mark.parametrize(
    "entry", ["torch==2.11.0", "torch==2.11.0+cu128", "other==1+local"]
)
def test_cpu_gate_rejects_unmapped_or_gpu_local_versions(tmp_path, entry):
    lock = tmp_path / "requirements.txt"
    lock.write_text(entry + " --hash=sha256:" + "0" * 64 + "\n")
    with pytest.raises(ValueError):
        audit_module().audit_requirements(lock)


@pytest.mark.parametrize(
    "dependency",
    [
        {"name": "torch", "version": "2.11.0", "skip_reason": "not found"},
        {"name": "torch", "version": "2.11.0", "vulns": [{"id": "KNOWN"}]},
    ],
)
def test_audit_never_accepts_success_with_skips_or_advisories(
    tmp_path, monkeypatch, dependency
):
    module = audit_module()
    lock, report = tmp_path / "lock", tmp_path / "report.json"
    lock.write_text("torch==2.11.0+cpu --hash=sha256:" + "0" * 64 + "\n")

    def run(args, **kwargs):
        report.write_text(json.dumps({"dependencies": [dependency]}))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(
        module.sys, "argv", ["audit", "--lock", str(lock), "--output", str(report)]
    )
    assert module.main() == 1

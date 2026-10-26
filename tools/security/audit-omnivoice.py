#!/usr/bin/env python3
"""Audit the optional CPU lock, including PyTorch's PEP 440 local versions.

PyPI's advisory API has no torch==X+cpu release. Query the public X version
without changing installed artifacts or discarding any advisory/severity.
Missing coverage is a failure, never a successful skip. No packages installed.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def audit_requirements(lock: Path) -> str:
    requirements = []
    for line in lock.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "--")):
            continue
        match = re.fullmatch(
            r"([A-Za-z0-9_.-]+)==([^ ]+) --hash=sha256:[0-9a-f]{64}", line
        )
        if not match:
            raise ValueError("unsupported lock entry")
        name, version = match.groups()
        if name.lower() in {"torch", "torchaudio"}:
            if not version.endswith("+cpu"):
                raise ValueError("CPU wheel required")
            version = version.removesuffix("+cpu")
        elif "+" in version:
            raise ValueError("unmapped local version")
        requirements.append(f"{name}=={version}")
    if not requirements:
        raise ValueError("empty lock")
    return "\n".join(requirements) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lock", type=Path, default=Path("deploy/omnivoice-worker.lock")
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    requirements = audit_requirements(args.lock)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="amora-worker-audit-") as directory:
        source = Path(directory) / "advisory-versions.txt"
        source.write_text(requirements, encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip_audit",
                "-r",
                str(source),
                "--no-deps",
                "--disable-pip",
                "--progress-spinner",
                "off",
                "--format",
                "json",
                "--output",
                str(args.output),
            ],
            check=False,
        )
    if not args.output.is_file():
        return result.returncode or 1
    data = json.loads(args.output.read_text(encoding="utf-8"))
    dependencies = data.get("dependencies", [])
    complete = len(dependencies) == len(requirements.splitlines()) and not any(
        dependency.get("skip_reason") for dependency in dependencies
    )
    affected = sum(bool(dependency.get("vulns")) for dependency in dependencies)
    print(
        f"Worker dependency audit: packages={len(dependencies)} coverage_complete={complete} affected_packages={affected}"
    )
    return result.returncode or (0 if complete and not affected else 1)


if __name__ == "__main__":
    raise SystemExit(main())

"""Print non-secret deployment intent for CI and an operator to inspect.

Does not contact hosts or databases, read secrets, alter production, or cut over.
Changing ACTIVE_DEPLOYMENT_PROFILE in core/amora/deployment_profiles.py is an
explicit code change that must pass review and deployment gates.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))
from amora.deployment_profiles import release_plan


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) == 2 else None
    print(json.dumps(release_plan(name), sort_keys=True, indent=2))

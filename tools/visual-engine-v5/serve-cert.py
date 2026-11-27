"""CI-only actual Uvicorn/Core server. No provider calls or production data."""
import os
from pathlib import Path
import uvicorn
from amora.app import AmoraCore
from amora.api import create_api

if __name__ == "__main__":
    os.environ["AMORA_MODE"] = "local"
    os.environ["AMORA_HOME"] = str(Path(".cert-core").resolve())
    os.environ["AMORA_VISUAL_V5_STAGING_ROOT"] = str(Path(".build").resolve())
    core = AmoraCore.bootstrap()
    try:
        code = core.pairing.issue().code
        path = Path(".cert-core/pairing-test.txt")
        path.write_text(code)
        path.chmod(0o600)
        uvicorn.run(create_api(core), host="127.0.0.1", port=8767, ws="wsproto", lifespan="off", access_log=False)
    finally:
        core.close()

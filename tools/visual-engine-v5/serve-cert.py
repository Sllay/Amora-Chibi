"""CI-only actual Uvicorn/Core server. No provider calls or production data."""
import os
from pathlib import Path
import uvicorn
from amora.app import AmoraCore
from amora.api import create_api
from amora.security import issue_browser_token

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
        token_path = Path(".cert-core/web-test-token.txt")
        token_path.write_text(issue_browser_token(core.secrets.get("LOCAL_API_TOKEN"),core.clock,"web"))
        token_path.chmod(0o600)
        uvicorn.run(create_api(core), host="127.0.0.1", port=8767, ws="wsproto", lifespan="off", access_log=False)
    finally:
        core.close()

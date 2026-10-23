"""Modal adapter only. Run explicitly after bundle/cost review; never auto-deploy."""
import os
from pathlib import Path
import modal

ROOT=Path(__file__).resolve().parents[2]
BUNDLE=Path(os.environ['AMORA_VOICE_BUNDLE']).resolve()
# This module is imported locally by Modal CLI; validate before image creation.
from amora.voice.bundle import verify_bundle
verify_bundle(BUNDLE)

image=(modal.Image.debian_slim(python_version='3.12')
    .pip_install('fastapi==0.161.1','pydantic==2.14.0b2','httpx==0.28.1','sherpa-onnx==1.13.8')
    .add_local_dir(str(ROOT/'core'),'/opt/amora/core',copy=True,ignore=['**/__pycache__/**','**/*.egg-info/**'])
    .add_local_dir(str(BUNDLE),'/opt/amora/voice',copy=True)
    .env({'PYTHONPATH':'/opt/amora/core','AMORA_HOME':'/opt/amora/voice'}))
app=modal.App('amora-voice-worker')

@app.function(image=image,cpu=2,memory=2048,timeout=120,max_containers=1,
              secrets=[modal.Secret.from_name('amora-voice-service')])
@modal.asgi_app()
def voice_worker():
    from amora.voice.bundle import verify_bundle
    from amora.voice.worker import application
    verify_bundle(Path('/opt/amora/voice'))
    return application()

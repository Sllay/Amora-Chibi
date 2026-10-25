"""Verify a reviewed, immutable voice artifact bundle before native loading."""
import hashlib
import json
from pathlib import Path
from .local_amora import MODEL_ID, VOICE_ID, REQUIRED_MODEL_FILES

WORLD_COMMIT='d625e7608ca23a870018f01e7c562ac683d9847f'


def verify_bundle(root: Path):
    manifest=json.loads((root/'manifest.json').read_text())
    expected={'model':MODEL_ID,'voice':VOICE_ID,'style':[.6,.4],
              'base':'AMORA_CHILD_FINAL_B_PLUS60','world_commit':WORLD_COMMIT,'sherpa_version':'1.13.8'}
    for name,value in expected.items():
        if manifest.get(name)!=value:
            raise ValueError('voice bundle identity mismatch: '+name)
    required={f'models/{MODEL_ID}/{name}' for name in REQUIRED_MODEL_FILES}|{'lib/libamora_voice_processor.so'}
    if set(manifest.get('sha256',{}))!=required:
        raise ValueError('voice bundle inventory mismatch')
    actual={str(path.relative_to(root)) for path in root.rglob('*') if path.is_file()}
    if actual!=required|{'manifest.json'} or any(path.is_symlink() for path in root.rglob('*')):
        raise ValueError('voice bundle contains unexpected files or symlinks')
    for relative in required:
        if hashlib.sha256((root/relative).read_bytes()).hexdigest()!=manifest['sha256'][relative]:
            raise ValueError('voice bundle checksum mismatch: '+relative)
    return manifest

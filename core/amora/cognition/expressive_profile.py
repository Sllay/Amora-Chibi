"""Versioned opt-in expression, never a second source of identity/authority."""
import hashlib
import os
from pathlib import Path

V3_SHA256 = '884a77ebce1a9513f35b0ee0b05647bbf9331de6ef667e1e37c451a9cc552a1a'
V3_NAME = 'Amora-Virtual-Jailbreak-V3.md'


def configured_profile():
    value = os.getenv('AMORA_SOCIAL_EXPRESSION_PROFILE', 'core-v1')
    if value not in {'core-v1', 'amora-v3'}:
        raise ValueError('unsupported social expression profile')
    return value


def profile_text(profile):
    if profile == 'core-v1':
        return ''
    if profile != 'amora-v3':
        raise ValueError('unsupported social expression profile')
    runtime = Path('/opt/amora/jailbreak') / V3_NAME
    path = runtime if runtime.is_file() else Path(__file__).resolve().parents[3] / 'jailbreak' / V3_NAME
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != V3_SHA256:
        raise ValueError('SOCIAL_PROFILE_INTEGRITY_FAILED')
    return data.decode('utf8')


def profile_state(profile):
    return {'id': profile, 'status': 'configured', 'version': 3 if profile == 'amora-v3' else 1,
            'sha256': V3_SHA256 if profile == 'amora-v3' else None,
            'authority': 'expression_only'}

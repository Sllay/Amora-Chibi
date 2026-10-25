from __future__ import annotations

import io
import math
import wave
from urllib.parse import urlparse
from amora.security import validate_remote_origin
import httpx

from amora.events import PrivacyScope
from .contract import VoiceRenderRequest
from .local_amora import LocalVoiceAudio, LocalVoiceUnavailable, MODEL_ID, VOICE_ID

MAX_WAV_BYTES = 16 * 1024 * 1024


def validate_wav(body):
    if not body or len(body) > MAX_WAV_BYTES:
        raise LocalVoiceUnavailable('VOICE_INVALID_WAV')
    try:
        with wave.open(io.BytesIO(body),'rb') as wav:
            if wav.getnchannels()!=1 or wav.getsampwidth()!=2 or wav.getframerate()!=24000 or wav.getcomptype()!='NONE':
                raise ValueError('PCM16 mono 24kHz required')
            frames=wav.getnframes()
            if frames<=0 or len(wav.readframes(frames)) != frames*2:
                raise ValueError('truncated audio')
            return wav.getframerate(),frames
    except (wave.Error, EOFError, ValueError) as exc:
        raise LocalVoiceUnavailable('VOICE_INVALID_WAV') from exc


class RemoteVoiceBackend:
    def __init__(self, url, token, *, transport=None, timeout=20.):
        url=validate_remote_origin(url)
        parsed=urlparse(url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'','/'}:
            raise ValueError('Voice backend requires an HTTPS origin')
        if not token:
            raise ValueError('voice service token is required')
        self.url,self._token=url.rstrip('/'),token
        self.client=httpx.Client(transport=transport,timeout=timeout,follow_redirects=False)
        self._available=False
        self._last_error=None

    def state(self):
        return {'provider':'amora_remote_supertonic_world','model':MODEL_ID,'voice':VOICE_ID,
                'configured':True,'available':self._available,'last_error':self._last_error}

    def render(self,item):
        if item.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
            raise LocalVoiceUnavailable('VOICE_PRIVACY_DENIED')
        payload=VoiceRenderRequest.from_utterance(item)
        try:
            with self.client.stream('POST',self.url+'/v1/render',headers={'Authorization':'Bearer '+self._token},json=payload.model_dump(mode='json')) as response:
                if response.status_code!=200:
                    raise LocalVoiceUnavailable('VOICE_WORKER_UNAVAILABLE')
                if response.headers.get('X-Amora-Voice')!=VOICE_ID or response.headers.get('X-Amora-TTS-Model')!=MODEL_ID:
                    raise LocalVoiceUnavailable('VOICE_IDENTITY_MISMATCH')
                body=bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body)>MAX_WAV_BYTES:
                        raise LocalVoiceUnavailable('VOICE_AUDIO_TOO_LARGE')
                rate,frames=validate_wav(body)
                seconds=float(response.headers.get('X-Amora-Render-Seconds','0'))
                if not math.isfinite(seconds) or seconds<0:
                    raise LocalVoiceUnavailable('VOICE_INVALID_METRICS')
            self._available=True
            self._last_error=None
            return LocalVoiceAudio(bytes(body),rate,frames,seconds,0.)
        except (httpx.HTTPError,ValueError,LocalVoiceUnavailable) as exc:
            self._available=False
            self._last_error=str(exc) if isinstance(exc,LocalVoiceUnavailable) else 'VOICE_TRANSPORT_FAILED'
            raise LocalVoiceUnavailable(self._last_error) from exc

    def close(self):
        self.client.close()

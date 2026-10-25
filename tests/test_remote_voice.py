import io
import json
import wave
from types import SimpleNamespace
import httpx
import pytest
from fastapi.testclient import TestClient
from amora.voice.worker import create_worker
from amora.voice.remote import RemoteVoiceBackend
from amora.voice.local_amora import LocalVoiceAudio, LocalVoiceUnavailable, VOICE_ID, MODEL_ID
from amora.api import create_api
from test_web_safari_v101 import _client, _enqueue_web


def wav_bytes():
    stream=io.BytesIO()
    with wave.open(stream,'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
        wav.writeframes(b'\x00\x00'*240)
    return stream.getvalue()


class Renderer:
    def __init__(self):
        self.inputs=[]
        self.failed=False
        self._world=SimpleNamespace(_ensure_loaded=lambda:None)

    def _ensure_engine(self):
        if self.failed:
            raise RuntimeError('do-not-expose-worker-secret')

    def render(self,item):
        self._ensure_engine()
        self.inputs.append(item)
        return LocalVoiceAudio(wav_bytes(),24000,240,.01,.01)


def test_worker_auth_schema_and_real_readiness():
    renderer=Renderer()
    with TestClient(create_worker(renderer,'service-test')) as client:
        assert client.post('/v1/render',json={'text':'Oi'}).status_code==401
        headers={'Authorization':'Bearer service-test'}
        assert client.get('/ready',headers=headers).status_code==200
        for extra in [{'memory':[]},{'user_auth':'private'},{'voice':'other-voice'}, {'expression':{'rate_scale':4}}]:
            assert client.post('/v1/render',json={'text':'Oi',**extra},headers=headers).status_code==422
        result=client.post('/v1/render',json={'text':'Oi'},headers=headers)
        assert result.status_code==200
        assert result.content==wav_bytes()
        renderer.failed=True
        assert client.get('/ready',headers=headers).status_code==503
        failure=client.post('/v1/render',json={'text':'Oi'},headers=headers)
        assert failure.status_code==503
        assert 'do-not-expose' not in failure.text


def test_core_remote_voice_success_minimal_payload_and_delivery(tmp_path,monkeypatch):
    core,_,headers=_client(tmp_path,monkeypatch)
    renderer=Renderer()
    calls=[]
    with TestClient(create_worker(renderer,'service-test')) as worker:
        def transport(request):
            calls.append(json.loads(request.content))
            result=worker.post('/v1/render',content=request.content,headers={'Authorization':request.headers['authorization'],'Content-Type':'application/json'})
            return httpx.Response(result.status_code,headers=result.headers,content=result.content)
        backend=RemoteVoiceBackend('https://voice.example','service-test',transport=httpx.MockTransport(transport))
        try:
            item=_enqueue_web(core,'Oi, BK.','remote-voice')
            item.metadata['private_context']='never-forward'
            with TestClient(create_api(core,voice=backend)) as client:
                claim=client.get('/v1/conversation/next?device_id=WEB_SAFARI',headers=headers)
                assert claim.json()['utterance']['id']==str(item.id)
                result=client.get(f'/v1/web/voice/utterances/{item.id}/audio',headers=headers)
                assert result.status_code==200 and result.content==wav_bytes()
                assert result.headers['x-amora-tts-provider']=='amora-remote'
                assert core.conversation.store.get_utterance(item.id).status.value=='claimed'
                payload=calls[0]
                assert set(payload)=={'protocol','model','voice','text','rate','volume','expression'}
                assert 'never-forward' not in json.dumps(payload)
                assert not hasattr(renderer.inputs[0],'metadata')
                client.post(f'/v1/conversation/utterances/{item.id}/ack',json={'spoken':True},headers=headers)
                assert core.conversation.store.get_utterance(item.id).status.value=='spoken'
        finally:
            core.close()


@pytest.mark.parametrize('outcome',['offline','invalid-wav','wrong-identity','cancelled'])
def test_remote_voice_failure_keeps_text_and_next_turn(tmp_path,monkeypatch,outcome):
    core,_,headers=_client(tmp_path,monkeypatch)
    item=_enqueue_web(core,'Texto continua.','voice-fail')
    def transport(request):
        if outcome=='offline':
            raise httpx.ReadTimeout('do-not-expose-network-detail')
        if outcome=='cancelled':
            core.conversation.store.cancel(item.id)
        return httpx.Response(200,content=b'broken' if outcome=='invalid-wav' else wav_bytes(),headers={
            'X-Amora-Voice':'wrong' if outcome=='wrong-identity' else VOICE_ID,'X-Amora-TTS-Model':MODEL_ID})
    backend=RemoteVoiceBackend('https://voice.example','service-test',transport=httpx.MockTransport(transport))
    try:
        with TestClient(create_api(core,voice=backend)) as client:
            client.get('/v1/conversation/next?device_id=WEB_SAFARI',headers=headers)
            result=client.get(f'/v1/web/voice/utterances/{item.id}/audio',headers=headers)
            assert result.status_code==(409 if outcome=='cancelled' else 503)
            assert 'do-not-expose' not in result.text
            assert core.conversation.store.get_utterance(item.id).text=='Texto continua.'
            client.post(f'/v1/conversation/utterances/{item.id}/cancel',json={'reason':'voice_failure'},headers=headers)
            next_item=_enqueue_web(core,'Próximo texto.','voice-next')
            response=client.get('/v1/conversation/next?device_id=WEB_SAFARI',headers=headers)
            assert response.json()['utterance']['id']==str(next_item.id)
            assert client.get('/ready').status_code==200
    finally:
        core.close()


def test_voice_missing_config_fails_closed():
    with pytest.raises(ValueError):
        RemoteVoiceBackend('https://voice.example','')
    with pytest.raises(ValueError):
        create_worker(Renderer(),'')


def test_voice_bundle_integrity_and_identity(tmp_path):
    import hashlib
    from amora.voice.bundle import verify_bundle, WORLD_COMMIT
    from amora.voice.local_amora import REQUIRED_MODEL_FILES
    required=[f'models/{MODEL_ID}/{name}' for name in REQUIRED_MODEL_FILES]+['lib/libamora_voice_processor.so']
    for name in required:
        path=tmp_path/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(b'test-fixture-artifact')
    manifest={'model':MODEL_ID,'voice':VOICE_ID,'style':[.6,.4],'base':'AMORA_CHILD_FINAL_B_PLUS60',
        'world_commit':WORLD_COMMIT,'sherpa_version':'1.13.8',
        'sha256':{name:hashlib.sha256((tmp_path/name).read_bytes()).hexdigest() for name in required}}
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    verify_bundle(tmp_path)
    (tmp_path/required[0]).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='checksum'):
        verify_bundle(tmp_path)

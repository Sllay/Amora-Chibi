"""Service/client boundaries with official renderer mocked, real WAV and API."""

import json
import os
import subprocess
import sys
import threading
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

from amora.api import create_api
from amora.events import PrivacyScope
from amora.voice.local_amora import LocalVoiceUnavailable
from amora.voice.remote import RemoteVoiceBackend
from amora.voice.renderers import renderer_from_environment
from amora.voice.worker import create_worker
from test_web_safari_v101 import _client, _enqueue_web
import test_omnivoice_renderer

mocked_omni = test_omnivoice_renderer.mocked_omni
reference_assets = test_omnivoice_renderer.reference_assets

TOKEN = "worker-service-only-test-32-bytes-0000"


def payload():
    return {
        "protocol": "amora.voice.render.v2",
        "text": "Oi, pai...",
        "cues": ["LAUGHTER"],
        "identity": {
            "request_id": str(uuid4()),
            "utterance_id": str(uuid4()),
            "turn_id": str(uuid4()),
        },
    }


def test_v2_worker_auth_headers_and_v1_does_not_silently_switch_renderer(mocked_omni):
    renderer, model, _ = mocked_omni
    headers = {"Authorization": "Bearer " + TOKEN}
    with TestClient(create_worker(renderer, TOKEN)) as client:
        assert client.get("/live").json() == {"status": "alive"}
        assert client.get("/ready").status_code == 401
        assert client.post("/v1/render", json=payload()).status_code == 401
        assert client.post("/v1/render", json=payload(), headers={"Authorization": "Bearer browser-only-token"}).status_code == 401
        ready = client.get("/ready", headers=headers)
        assert ready.status_code == 200 and ready.json()["streaming_capable"] is False
        data = payload()
        result = client.post("/v1/render", json=data, headers=headers)
        assert result.status_code == 200
        assert result.headers["X-Amora-Voice-Renderer"] == "omnivoice"
        assert result.headers["X-Amora-Voice-Model"] == "k2-fsa/OmniVoice"
        assert result.headers["X-Amora-Voice"] == "LAB_TEST_NOT_CANONICAL"
        assert result.headers["X-Amora-Sample-Rate"] == "24000"
        assert result.headers["X-Amora-Streaming-Capable"] == "false"
        assert result.headers["X-Amora-Request-Id"] == data["identity"]["request_id"]
        assert (
            client.post("/v1/render", json={"text": "Oi"}, headers=headers).status_code
            == 409
        )
        assert len(model.calls) == 1


@pytest.mark.parametrize(
    "extra",
    [
        {"renderer": "supertonic"},
        {"model": "other"},
        {"reference": "url"},
        {"reference_audio": "ref"},
        {"num_step": 16},
        {"guidance_scale": 3},
        {"class_temperature": 1},
        {"AUTH": "private"},
        {"SECRET": "private"},
        {"privacy_scope": "AUTH"},
        {"memory": []},
        {"cues": ["UNKNOWN"]},
        {"expression": {"rate_scale": 8}},
    ],
)
def test_request_cannot_select_renderer_reference_tuning_or_send_secret_context(
    mocked_omni, extra
):
    renderer, model, _ = mocked_omni
    with TestClient(create_worker(renderer, TOKEN)) as client:
        result = client.post(
            "/v1/render",
            json={**payload(), **extra},
            headers={"Authorization": "Bearer " + TOKEN},
        )
        assert result.status_code == 422 and result.json() == {
            "detail": "invalid request"
        }
    assert not model.calls


def test_mismatch_reference_ready_false_live_true_no_data_leak(
    mocked_omni, reference_assets
):
    renderer, model, _ = mocked_omni
    reference_assets[1].write_bytes(b"bad-prompt-private")
    with TestClient(create_worker(renderer, TOKEN)) as client:
        assert client.get("/live").status_code == 200
        failure = client.get("/ready", headers={"Authorization": "Bearer " + TOKEN})
        assert failure.status_code == 503
        assert "private" not in failure.text and "reference" not in failure.text
        assert not model.calls


def test_remote_v2_minimal_payload_and_audio_identity_through_core(
    tmp_path, monkeypatch, mocked_omni
):
    core, _, headers = _client(tmp_path, monkeypatch)
    renderer, model, _ = mocked_omni
    calls = []
    with TestClient(create_worker(renderer, TOKEN)) as worker:

        def transport(request):
            data = json.loads(request.content)
            calls.append(data)
            result = worker.post(
                "/v1/render",
                content=request.content,
                headers={
                    "Authorization": request.headers["authorization"],
                    "Content-Type": "application/json",
                },
            )
            return httpx.Response(
                result.status_code, headers=result.headers, content=result.content
            )

        backend = RemoteVoiceBackend(
            "https://voice.example",
            TOKEN,
            protocol="amora.voice.render.v2",
            expected_identity=renderer.identity,
            transport=httpx.MockTransport(transport),
        )
        try:
            item = _enqueue_web(core, "Texto literal kkkk 😂", "voice-v2")
            # Only explicitly allowlisted neutral cues, not the other metadata.
            item.metadata.update(
                {
                    "cognitive_turn_id": str(uuid4()),
                    "cues": ["LAUGHTER"],
                    "private_context": "never-forward",
                }
            )
            audio = backend.render(item)
            assert audio.sample_rate == 24000
            assert set(calls[0]) == {
                "protocol",
                "text",
                "rate",
                "volume",
                "expression",
                "cues",
                "identity",
            }
            assert calls[0]["cues"] == ["LAUGHTER"]
            assert calls[0]["identity"]["utterance_id"] == str(item.id)
            assert not any(
                secret in json.dumps(calls)
                for secret in ["never-forward", TOKEN, "PRIVATE", "LOCAL_API_TOKEN"]
            )
            with TestClient(create_api(core, voice=backend)) as client:
                client.get(
                    "/v1/conversation/next?device_id=WEB_SAFARI", headers=headers
                )
                response = client.get(
                    f"/v1/web/voice/utterances/{item.id}/audio", headers=headers
                )
                assert response.status_code == 200
                assert response.headers["X-Amora-TTS-Model"] == "k2-fsa/OmniVoice"
                assert response.headers["X-Amora-Voice"] == renderer.identity.voice
                assert "Steps" not in response.headers
                assert (
                    core.conversation.store.get_utterance(item.id).text
                    == "Texto literal kkkk 😂"
                )
        finally:
            backend.close()
            core.close()


@pytest.mark.parametrize(
    "header",
    [
        "X-Amora-Voice-Renderer",
        "X-Amora-Voice-Model",
        "X-Amora-Voice",
        "X-Amora-Sample-Rate",
        "X-Amora-Streaming-Capable",
        "X-Amora-Request-Id",
        "X-Amora-Utterance-Id",
        "X-Amora-Turn-Id",
        "X-Amora-Protocol",
    ],
)
def test_worker_identity_mismatch_fail_closed_for_audio_preserves_text(
    tmp_path, monkeypatch, mocked_omni, header
):
    core, _, headers = _client(tmp_path, monkeypatch)
    renderer, model, _ = mocked_omni
    calls = []
    with TestClient(create_worker(renderer, TOKEN)) as worker:

        def transport(request):
            calls.append(1)
            result = worker.post(
                "/v1/render",
                content=request.content,
                headers={
                    "Authorization": "Bearer " + TOKEN,
                    "Content-Type": "application/json",
                },
            )
            changed = dict(result.headers)
            changed[header.lower()] = "mismatch"
            return httpx.Response(
                result.status_code, headers=changed, content=result.content
            )

        backend = RemoteVoiceBackend(
            "https://voice.example",
            TOKEN,
            protocol="amora.voice.render.v2",
            expected_identity=renderer.identity,
            transport=httpx.MockTransport(transport),
        )
        try:
            item = _enqueue_web(core, "Meu texto continua aqui.", "identity-failure")
            with TestClient(create_api(core, voice=backend)) as client:
                claim = client.get(
                    "/v1/conversation/next?device_id=WEB_SAFARI", headers=headers
                )
                assert claim.json()["utterance"]["text"] == item.text
                result = client.get(
                    f"/v1/web/voice/utterances/{item.id}/audio", headers=headers
                )
                assert (
                    result.status_code == 503
                    and result.json()["detail"] == "VOICE_IDENTITY_MISMATCH"
                )
                assert core.conversation.store.get_utterance(item.id).text == item.text
                assert len(calls) == 1  # no retry or fallback
        finally:
            backend.close()
            core.close()


@pytest.mark.parametrize("scope", [PrivacyScope.AUTH, PrivacyScope.SECRET])
def test_auth_secret_never_leave_core(tmp_path, monkeypatch, scope):
    core, _, _ = _client(tmp_path, monkeypatch)
    item = _enqueue_web(core, "private", "privacy-test").model_copy(
        update={"privacy_scope": scope}
    )
    calls = []
    backend = RemoteVoiceBackend(
        "https://voice.example",
        TOKEN,
        transport=httpx.MockTransport(lambda r: calls.append(r)),
    )
    try:
        with pytest.raises(LocalVoiceUnavailable, match="VOICE_PRIVACY_DENIED"):
            backend.render(item)
        assert not calls
    finally:
        backend.close()
        core.close()


@pytest.mark.parametrize("outcome", ["transport", "503"])
def test_render_transport_failure_never_retried(tmp_path, monkeypatch, outcome):
    core, _, _ = _client(tmp_path, monkeypatch)
    calls = []

    def transport(request):
        calls.append(1)
        if outcome == "transport":
            raise httpx.ReadTimeout("private-error-secret", request=request)
        return httpx.Response(503, text="private-worker-detail")

    backend = RemoteVoiceBackend(
        "https://voice.example", TOKEN, transport=httpx.MockTransport(transport)
    )
    try:
        with pytest.raises(LocalVoiceUnavailable) as error:
            backend.render(_enqueue_web(core, "Texto.", "failure-test"))
        assert len(calls) == 1 and "private" not in str(error.value)
    finally:
        backend.close()
        core.close()


def test_timeout_keeps_slot_busy_and_readiness_does_not_generate(mocked_omni):
    renderer, model, _ = mocked_omni
    release = threading.Event()
    original = model.generate

    def slow(**kwargs):
        release.wait(2)
        return original(**kwargs)

    model.generate = slow
    headers = {"Authorization": "Bearer " + TOKEN}
    try:
        with TestClient(create_worker(renderer, TOKEN, render_timeout=0.02)) as client:
            first = client.post("/v1/render", json=payload(), headers=headers)
            assert first.status_code == 504
            assert client.post(
                "/v1/render", json=payload(), headers=headers
            ).json() == {"detail": "VOICE_BUSY"}
            assert client.get("/ready", headers=headers).status_code == 200
            assert not model.calls
            release.set()
    finally:
        release.set()


def test_server_selection_and_financial_fail_closed(monkeypatch, reference_assets):
    monkeypatch.setenv("AMORA_VOICE_RENDERER", "omnivoice")
    monkeypatch.delenv("AMORA_VOICE_ZERO_COST_CONFIRMED", raising=False)
    with pytest.raises(ValueError, match="FREE_CPU_HOST"):
        renderer_from_environment()
    monkeypatch.setenv("AMORA_VOICE_ZERO_COST_CONFIRMED", "true")
    monkeypatch.setenv("AMORA_OMNIVOICE_REFERENCE_MANIFEST", str(reference_assets[0]))
    monkeypatch.setenv("AMORA_OMNIVOICE_PROMPT", str(reference_assets[1]))
    assert renderer_from_environment().state()["renderer"] == "omnivoice"
    monkeypatch.setenv("AMORA_ZERO_COST_REQUIRED", "false")
    with pytest.raises(ValueError, match="ZERO_COST_REQUIRED"):
        renderer_from_environment()
    monkeypatch.setenv("AMORA_ZERO_COST_REQUIRED", "true")
    monkeypatch.setenv("AMORA_VOICE_RENDERER", "paid-auto")
    with pytest.raises(ValueError, match="UNSUPPORTED"):
        renderer_from_environment()


def test_core_image_and_import_graph_do_not_load_neural_runtime(tmp_path):
    source = """
import importlib.abc, sys
class DenyNeural(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'torch', 'torchaudio', 'omnivoice', 'sherpa_onnx'} or fullname in {'amora.voice.omnivoice_renderer', 'amora.voice.worker'}:
            raise AssertionError('Core imported worker runtime')
sys.meta_path.insert(0, DenyNeural())
from amora.app import AmoraCore
from amora.api import create_api
core = AmoraCore.bootstrap()
create_api(core)
assert core.readiness()['status'] == 'ready'
core.close()
"""
    env = dict(
        os.environ,
        AMORA_HOME=str(tmp_path / "isolated-core"),
        AMORA_MODE="local",
        AMORA_VOICE_BACKEND="remote",
        AMORA_VOICE_WORKER_URL="https://voice.example",
        AMORA_VOICE_SERVICE_TOKEN=TOKEN,
        AMORA_VOICE_PROTOCOL="amora.voice.render.v2",
        AMORA_VOICE_EXPECTED_RENDERER="omnivoice",
        AMORA_VOICE_EXPECTED_MODEL="k2-fsa/OmniVoice",
        AMORA_VOICE_EXPECTED_VOICE="LAB_TEST_NOT_CANONICAL",
    )
    result = subprocess.run(
        [sys.executable, "-c", source],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_v2_requires_explicit_expected_identity_and_cloud_service_token(
    monkeypatch, mocked_omni
):
    with pytest.raises(ValueError, match="pinned"):
        RemoteVoiceBackend(
            "https://voice.example", TOKEN, protocol="amora.voice.render.v2"
        )
    monkeypatch.setenv("AMORA_MODE", "cloud")
    with pytest.raises(ValueError, match="32 bytes"):
        create_worker(mocked_omni[0], "short")

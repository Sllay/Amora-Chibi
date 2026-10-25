import io
import logging
import sqlite3
import wave
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.audio.stt import GroqWhisperSTT
from amora.cognition.usage import UsageRecorder
from amora.cognition import CognitiveRequest
from amora.cognition.providers import ProviderError
from amora.models import TaskClass
from amora.observability import JsonFormatter
from amora.time import SystemClock
from test_cognition_v10 import FakeStructuredProvider
import test_remote_connection_recovery

fault_remote = test_remote_connection_recovery.fault_remote


class MeteredProvider(FakeStructuredProvider):
    def __init__(self, server, *, remaining_requests=100):
        super().__init__()
        self.server = server
        self.inject = None
        self.calls = 0
        self.remaining_requests = remaining_requests

    async def realize(self, context):
        self.calls += 1
        result = await super().realize(context)
        result["_usage"]["remaining_requests"] = self.remaining_requests
        if self.inject:
            self.server.arm(self.inject)
            self.inject = None
        return result


def valid_wav():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * 1600)
    return buffer.getvalue()


@pytest.mark.parametrize("fault", ["INSERT INTO model_usage", "COMMIT"])
def test_boot_dialogue_stt_usage_loss_then_second_stt_succeeds_without_restart(
    fault_remote, tmp_path, monkeypatch, caplog, fault
):
    backend, server = fault_remote
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "core"))
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    core = AmoraCore.bootstrap(storage=backend)
    monkeypatch.setenv("AMORA_MODE", "cloud")
    monkeypatch.setenv("AMORA_PUBLIC_ORIGINS", "https://amora.example")
    provider = MeteredProvider(server)
    core.cognition.register_provider(provider)
    core.secrets.set("GROQ_API_KEY", "recovery-groq-stt-secret")
    wav = valid_wav()
    stt_calls = []

    def groq(request):
        assert wav in request.content
        assert request.headers["authorization"] == "Bearer recovery-groq-stt-secret"
        stt_calls.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "text": "Amora, lembra daquele assunto?",
                "language": "pt",
                "segments": [
                    {"start": 0, "end": 0.1, "no_speech_prob": 0, "avg_logprob": -0.1}
                ],
            },
        )

    core.stt = GroqWhisperSTT(core.secrets, transport=httpx.MockTransport(groq))
    original_connection = core.cognition.store._conn
    try:
        with TestClient(
            create_api(core),
            base_url="https://amora.example",
            raise_server_exceptions=False,
        ) as client:
            token = client.post(
                "/v1/pairing/redeem", json={"code": core.pairing.issue().code}
            ).json()["token"]
            headers = {"Authorization": "Bearer " + token}
            first = client.post(
                "/v1/web/utterance",
                headers=headers,
                json={
                    "text": "Amora, lembra disso?",
                    "remote_processing_allowed": True,
                    "test_mode": True,
                },
            )
            assert first.status_code == 200 and first.json()["reply_queued"]
            assert provider.calls == server.count_sql("INSERT INTO model_usage") == 1
            provider.inject = fault
            caplog.set_level(logging.INFO)
            path = "/v1/audio/transcribe?speaker_id=PERSON_BK&speech_device_id=WEB_SAFARI&remote_processing_allowed=true&test_mode=true"
            bodies = []
            for _ in range(2):
                response = client.post(
                    path, headers={**headers, "Content-Type": "audio/wav"}, content=wav
                )
                assert response.status_code == 200, response.text
                body = response.json()
                bodies.append(body)
                assert (
                    body["accepted"]
                    and body["transcript"]["text"] == "Amora, lembra daquele assunto?"
                )
                assert body["turn"]["state"] == "TAKE_TURN" and body["test_mode"]
                item = body["reply_queued"]
                assert (
                    item
                    and item["display_text"]
                    == "Claro, papis 💜. Eu lembro disso — e queria comentar uma coisa."
                )
                turn = item["metadata"]["cognitive_turn_id"]
                claim = client.get(
                    f"/v1/conversation/next?device_id=WEB_SAFARI&cognitive_turn_id={turn}",
                    headers=headers,
                )
                assert (
                    claim.status_code == 200
                    and claim.json()["utterance"]["metadata"]["cognitive_turn_id"]
                    == turn
                )
                client.post(
                    f"/v1/conversation/utterances/{item['id']}/cancel",
                    headers=headers,
                    json={"reason": "test_voice_only", "speech_only": True},
                )
            assert len(stt_calls) == 2 and provider.calls == 3
            assert (
                server.failures == 1
                and server.count_sql("INSERT INTO model_usage") == 3
            )
            assert core.cognition.store._conn is original_connection
            assert not original_connection._broken and not original_connection._closed
            usage = core.cognition.store.usage_ledger()[0]
            assert usage["request_count"] == (3 if fault == "COMMIT" else 2)
            assert (
                usage["success_count"] == usage["request_count"]
            )  # No synthetic failure row.
            health = core.cognition.usage.state()
            assert health["status"] == "DEGRADED" and health["uncertain_count"] == 1
            assert health["last_success_at"] is not None
            admin = {"Authorization": "Bearer " + core.secrets.get("LOCAL_API_TOKEN")}
            snapshot = client.get("/v1/operations/state", headers=admin).json()
            assert snapshot["modules"]["usage_ledger"] == "DEGRADED"
            assert any(
                error["error_code"] == "USAGE_LEDGER_PERSISTENCE_UNCERTAIN"
                and error["occurrence_count"] == 1
                for error in snapshot["errors"]
            )
            assert client.get("/live").json() == {"status": "alive"}
            assert client.get("/ready").status_code == 200
            assert not list((core.paths.cache / "incoming").glob("*.wav"))
            logs = [
                r
                for r in caplog.records
                if r.name in {"amora.storage.remote", "amora.cognition.usage"}
            ]
            names = [r.message for r in logs]
            assert names == [
                "storage_connection_poisoned",
                "usage_ledger_persistence_uncertain",
                "storage_connection_recovered",
            ]
            uncertain = logs[1]
            assert (
                uncertain.storage_error_code == "SQL_TRANSPORT_FAILED_OUTCOME_UNKNOWN"
            )
            assert (
                uncertain.storage_module == "amora.cognition.store"
                and uncertain.operation == "record_usage"
            )
            safe_logs = "\n".join(JsonFormatter().format(record) for record in logs)
            for secret in [
                token,
                "recovery-groq-stt-secret",
                "recovery-db-token",
                "private-body",
                "do-not-log",
                "Amora, lembra daquele assunto?",
                "INSERT INTO",
                "Authorization",
            ]:
                assert secret not in safe_logs
            assert not any(r.getMessage() == "request_failed" for r in caplog.records)
    finally:
        core.close()


def test_quota_signals_remain_conservative_even_if_usage_is_uncertain(
    fault_remote, tmp_path, monkeypatch
):
    backend, server = fault_remote
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "core"))
    core = AmoraCore.bootstrap(storage=backend)
    provider = MeteredProvider(server, remaining_requests=0)
    core.cognition.register_provider(provider)
    provider.inject = "INSERT INTO model_usage"
    try:
        with TestClient(create_api(core)) as client:
            headers = {"Authorization": "Bearer " + core.secrets.get("LOCAL_API_TOKEN")}
            response = client.post(
                "/v1/web/utterance",
                headers=headers,
                json={
                    "text": "Amora, lembra disso?",
                    "remote_processing_allowed": True,
                },
            )
            assert response.status_code == 200 and response.json()["reply_queued"]
            assert (
                core.cognition.router.descriptor(
                    provider.descriptor.name
                ).quota_remaining
                == 0
            )
            assert provider.descriptor.name in core.cognition._cooldown_names()
    finally:
        core.close()


def test_mandatory_journal_transport_failure_still_fails_closed(
    fault_remote, tmp_path, monkeypatch
):
    backend, server = fault_remote
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "core"))
    core = AmoraCore.bootstrap(storage=backend)
    try:
        with TestClient(create_api(core), raise_server_exceptions=False) as client:
            headers = {"Authorization": "Bearer " + core.secrets.get("LOCAL_API_TOKEN")}
            server.arm("INSERT INTO cognitive_journal")
            response = client.post(
                "/v1/web/utterance", headers=headers, json={"text": "oi"}
            )
            assert response.status_code == 500
            assert core.cognition.usage.state()["uncertain_count"] == 0
            assert (
                client.post(
                    "/v1/web/utterance", headers=headers, json={"text": "oi"}
                ).status_code
                == 200
            )
    finally:
        core.close()


def test_usage_recorder_does_not_swallow_programming_errors_or_forge_ledger_rows():
    class Store:
        def record_usage(self, **kwargs):
            raise TypeError("programming bug")

    recorder = UsageRecorder(Store(), SystemClock())
    with pytest.raises(TypeError):
        recorder.record(provider="fixture")
    assert recorder.state()["status"] == "ON"


def test_confirmed_usage_constraint_failure_is_visible_not_reported_as_unknown(caplog):
    class Store:
        def record_usage(self, **kwargs):
            error = sqlite3.IntegrityError("private secret must never be logged")
            error.sqlite_errorname = "SQLITE_CONSTRAINT_NOTNULL"
            raise error

    recorder = UsageRecorder(Store(), SystemClock())
    assert recorder.record(turn_id=uuid4()) == "failed"
    assert (
        recorder.state()["failure_count"] == 1
        and recorder.state()["uncertain_count"] == 0
    )
    assert recorder.state()["last_error_code"] == "USAGE_LEDGER_PERSISTENCE_FAILED"
    assert "private secret" not in caplog.text


@pytest.mark.asyncio
async def test_uncertain_reflection_usage_does_not_borrow_quota_status_from_an_old_turn(
    fault_remote, tmp_path, monkeypatch
):
    backend, server = fault_remote
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "core"))
    core = AmoraCore.bootstrap(storage=backend)

    class Provider(FakeStructuredProvider):
        def __init__(self):
            super().__init__()
            self.descriptor = replace(
                self.descriptor, capabilities=frozenset({TaskClass.REASONING})
            )

        async def propose(self, context):
            server.arm("INSERT INTO model_usage")
            raise ProviderError("unavailable", status_code=503)

    provider = Provider()
    core.cognition.register_provider(provider)
    try:
        core.cognition.store.record_usage(
            provider=provider.descriptor.name,
            model="fixture",
            task_class="reasoning",
            success=False,
            status_code=429,
            turn_id=uuid4(),
        )
        request = CognitiveRequest(
            text="Amora, pensa nisso.",
            speaker_id="PERSON_BK",
            remote_processing_allowed=True,
        )
        result = await core.cognition.reflect(request)
        assert "REFLECTION_PROVIDER_FAILURE" in result.reason_codes
        assert (
            core.cognition.router.descriptor(provider.descriptor.name).quota_remaining
            is None
        )
        assert server.count_sql("INSERT INTO model_usage") == 2
        assert core.cognition.usage.state()["uncertain_count"] == 1
    finally:
        core.close()

import asyncio
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.messaging import ChannelType, RoutingAction
from test_messaging_lifecycle_stability import CountingStructuredProvider, _clock
from test_platform_messaging_step2 import _bind_bk, _message


class GatedProvider(CountingStructuredProvider):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def realize(self, context):
        self.entered.set()
        await self.release.wait()
        return await super().realize(context)


@pytest.mark.asyncio
async def test_progress_only_during_authorized_dm_and_cleared_on_finish(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "runtime"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        _bind_bk(core)
        provider = GatedProvider()
        core.cognition.register_provider(provider)
        incoming = _message(core, message_id="dm-1", actor_id="111111111111111111",
                            channel_type=ChannelType.DM, text="Me conta algo sobre jogos.")
        incoming = incoming.model_copy(update={"remote_processing_allowed": True})
        turn = core.messaging._turn_id(incoming)
        assert turn == UUID("1548fbdd-61ff-5104-adc0-43f1debb34bf")
        assert not core.messaging.progress_active(turn)
        task = asyncio.create_task(core.messaging.receive(incoming))
        try:
            await asyncio.wait_for(provider.entered.wait(), 2)
            assert core.messaging.progress_active(turn)
            provider.release.set()
            result = await task
            assert result.decision.action == RoutingAction.RESPOND
            assert not core.messaging.progress_active(turn)
        finally:
            provider.release.set()
            await asyncio.gather(task, return_exceptions=True)
        unknown = _message(core, message_id="unknown", actor_id="999999999999999999",
                           channel_type=ChannelType.DM)
        blocked = await core.messaging.receive(unknown)
        assert blocked.decision.action == RoutingAction.QUARANTINE
        assert not core.messaging.progress_active(core.messaging._turn_id(unknown))
    finally:
        core.close()


@pytest.mark.asyncio
async def test_group_generation_never_enables_dm_typing(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "runtime"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        provider = GatedProvider()
        core.cognition.register_provider(provider)
        incoming = _message(core, message_id="group", actor_id="111111111111111111",
                            channel_type=ChannelType.GROUP, mention=True,
                            text="Amora, me conta algo sobre jogos.").model_copy(
                                update={"remote_processing_allowed": True})
        task = asyncio.create_task(core.messaging.receive(incoming))
        try:
            await asyncio.wait_for(provider.entered.wait(), 2)
            assert not core.messaging.progress_active(core.messaging._turn_id(incoming))
        finally:
            provider.release.set()
            await task
    finally:
        core.close()


def test_progress_endpoint_is_authenticated_and_content_free(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "runtime"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        path = "/v1/messaging/worker/progress/1548fbdd-61ff-5104-adc0-43f1debb34bf"
        with TestClient(create_api(core)) as client:
            assert client.get(path).status_code == 401
            token = core.secrets.get("LOCAL_API_TOKEN")
            response = client.get(path, headers={"Authorization": "Bearer " + token})
            assert response.status_code == 200
            assert response.json() == {"active": False}
    finally:
        core.close()

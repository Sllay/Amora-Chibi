from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from amora.api import create_api
from amora.app import AmoraCore
from amora.security import issue_browser_token, token_role
from amora.time import SimulatedClock

def test_visual_scoped_snapshot_and_reconnect(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock(datetime(2026,11,26,tzinfo=timezone.utc)))
    try:
        client = TestClient(create_api(core))
        secret = core.secrets.get("LOCAL_API_TOKEN")
        token = issue_browser_token(secret, core.clock, "visual")
        assert token_role(token, secret, core.clock) == "visual"
        headers = {"origin":"http://testserver"}
        path = "/v1/avatar/visual-stream"
        protocols = ["amora.visual.v1", "auth."+token]
        # Starlette TestClient mutates the supplied headers via setdefault;
        # a fresh dictionary for each handshake prevents a rejected protocol
        # from contaminating subsequent valid and invalid test cases.
        for bad in ([], ["amora.visual.v1", "auth."+issue_browser_token(secret,core.clock,"avatar")], ["amora.visual.v1","auth."+secret]):
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(path,headers=dict(headers),subprotocols=bad):
                    pass
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(path+"?token=x",headers={**headers,"sec-websocket-protocol":", ".join(protocols)}):
                pass
        for invalid_origin in ({}, {"origin":"http://other.example"}):
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(path,headers=dict(invalid_origin),subprotocols=protocols):
                    pass
        assert client.get("/v1/avatar/project",headers={"Authorization":"Bearer "+token}).status_code==403
        assert "sec-websocket-protocol" not in headers
        with client.websocket_connect(path,headers=dict(headers),subprotocols=protocols) as ws:
            a=ws.receive_json()
            assert a["kind"]=="snapshot" and a["sequence"]==1 and a["version"]==1
            assert set(a["state"])=={"expression","emotion","intensity","gesture","speaking","blink_allowed","blush","arousal","valence"}
            assert ws.accepted_subprotocol=="amora.visual.v1"
            ws.send_text("forbidden")
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
        with client.websocket_connect(path,headers=dict(headers),subprotocols=protocols) as ws:
            b=ws.receive_json()
            assert b["session"]!=a["session"] and b["sequence"]==1
    finally:
        core.close()


def test_visual_audio_relay_is_scoped_and_silent_on_disconnect(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock(datetime(2026,11,26,tzinfo=timezone.utc)))
    try:
        client = TestClient(create_api(core))
        secret = core.secrets.get("LOCAL_API_TOKEN")
        visual = issue_browser_token(secret,core.clock,"visual")
        web = issue_browser_token(secret,core.clock,"web")
        def headers():
            return {"origin":"http://testserver"}
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/v1/avatar/visual-audio",headers=headers(),subprotocols=["amora.visual.audio.v1","auth."+visual]):
                pass
        with client.websocket_connect("/v1/avatar/visual-stream",headers=headers(),subprotocols=["amora.visual.v1","auth."+visual]) as subscriber:
            assert subscriber.receive_json()["kind"]=="snapshot"
            with client.websocket_connect("/v1/avatar/visual-audio",headers=headers(),subprotocols=["amora.visual.audio.v1","auth."+web]) as publisher:
                publisher.send_json({"version":1,"level":0.7})
                msg=subscriber.receive_json()
                assert msg["kind"]=="audio" and msg["level"]==0.7
            msg=subscriber.receive_json()
            assert msg["kind"]=="audio" and msg["level"]==0
    finally:
        core.close()

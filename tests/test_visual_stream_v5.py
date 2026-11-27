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
        for bad in ([], ["amora.visual.v1", "auth."+issue_browser_token(secret,core.clock,"avatar")], ["amora.visual.v1","auth."+secret]):
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(path,headers=headers,subprotocols=bad):
                    pass
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(path+"?token=x",headers={**headers,"sec-websocket-protocol":", ".join(protocols)}):
                pass
        assert client.get("/v1/avatar/project",headers={"Authorization":"Bearer "+token}).status_code==403
        with client.websocket_connect(path,headers=headers,subprotocols=protocols) as ws:
            a=ws.receive_json()
            assert a["kind"]=="snapshot" and a["sequence"]==1 and a["version"]==1
            assert set(a["state"])=={"expression","emotion","intensity","gesture","speaking","blink_allowed","blush","arousal","valence"}
            assert ws.accepted_subprotocol=="amora.visual.v1"
            ws.send_text("forbidden")
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
        with client.websocket_connect(path,headers=headers,subprotocols=protocols) as ws:
            b=ws.receive_json()
            assert b["session"]!=a["session"] and b["sequence"]==1
    finally:
        core.close()

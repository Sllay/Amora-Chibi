import hashlib,json
import pytest
from amora.avatar import visual_preview as v

def test_preview_rejects_missing_manifest_and_tampering(tmp_path,monkeypatch):
    with pytest.raises(RuntimeError):
        v.verify_preview(tmp_path)
    core=b"fixture SDK only"
    moc=b"MOC3 fixture"
    sha=lambda b: hashlib.sha256(b).hexdigest()
    monkeypatch.setattr(v,"CORE_SHA",sha(core))
    monkeypatch.setattr(v,"MOC_SHA",sha(moc))
    files={name:b"fixture" for name in v.REQUIRED}
    files["vendor/live2dcubismcore.min.js"]=core
    files["model/model.moc3"]=moc
    files["identity.json"]=json.dumps({"coreSha256":sha(core),"mocSha256":sha(moc),"build":"a"*40}).encode()
    for name,data in files.items():
        p=tmp_path/name
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(data)
    (tmp_path/"manifest.json").write_text(json.dumps({"version":1,"files":{name:sha(data) for name,data in files.items()}}))
    v.verify_preview(tmp_path)
    (tmp_path/"extra.txt").write_text("not in manifest")
    with pytest.raises(RuntimeError):
        v.verify_preview(tmp_path)
    (tmp_path/"extra.txt").unlink()
    (tmp_path/"app.js").write_text("changed")
    with pytest.raises(RuntimeError):
        v.verify_preview(tmp_path)


@pytest.mark.parametrize("failure", ["disabled", "manifest_missing", "password_invalid", "manifest_corrupt"])
def test_optional_preview_failure_keeps_core_online_and_blocks_files(tmp_path, monkeypatch, caplog, failure):
    import base64
    from amora.app import AmoraCore
    from amora.api import create_api
    from fastapi.testclient import TestClient
    from amora.time import SimulatedClock
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "home"))
    root = tmp_path / "preview"
    root.mkdir()
    (root / "index.html").write_text("must-not-be-served")
    password = "x" * 40
    monkeypatch.setenv("AMORA_VISUAL_V5_PREVIEW_PASSWORD", password)
    if failure == "disabled":
        monkeypatch.delenv("AMORA_VISUAL_V5_STAGING_ROOT", raising=False)
    else:
        monkeypatch.setenv("AMORA_VISUAL_V5_STAGING_ROOT", str(root))
    if failure == "password_invalid":
        monkeypatch.setenv("AMORA_VISUAL_V5_PREVIEW_PASSWORD", "short")
    elif failure == "manifest_corrupt":
        (root / "manifest.json").write_text("{invalid")
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        app = create_api(core)
        assert app.state.visual_preview_status == ("disabled" if failure == "disabled" else "unavailable")
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/visual-engine/").status_code == 200
            assert client.get("/avatar/").status_code == 200
            admin = {"Authorization": "Bearer " + core.secrets.get("LOCAL_API_TOKEN")}
            assert client.get("/v1/avatar/project", headers=admin).status_code == 200
            basic = {"Authorization": "Basic " + base64.b64encode(("preview:"+password).encode()).decode()}
            for path in ("/avatar/v5", "/avatar/v5/", "/avatar/v5/index.html", "/avatar/v5/vendor/live2dcubismcore.min.js"):
                for headers in ({}, basic):
                    response = client.get(path, headers=headers)
                    assert response.status_code == (404 if failure == "disabled" else 503)
                    assert "must-not-be-served" not in response.text
        if failure != "disabled":
            assert "VISUAL_V5_PREVIEW_DISABLED" in caplog.text
        assert password not in caplog.text
    finally:
        core.close()

def test_prism_link_scope_and_both_passwords(tmp_path, monkeypatch):
    import base64
    from amora.app import AmoraCore
    from amora.api import create_api
    from amora.time import SimulatedClock
    from amora.security import token_role
    from fastapi.testclient import TestClient
    from amora.avatar import prism_link
    password="p"*40
    master="d"*40
    monkeypatch.setenv("AMORA_HOME",str(tmp_path/"home"))
    monkeypatch.setenv("AMORA_DEV_MASTER_KEY",master)
    monkeypatch.setenv("AMORA_VISUAL_V5_PREVIEW_PASSWORD",password)
    root=tmp_path/"preview";root.mkdir()
    monkeypatch.setenv("AMORA_VISUAL_V5_STAGING_ROOT",str(root))
    monkeypatch.setattr(v,"verify_preview",lambda path:None)
    for name in ("index.html","app.js","live-overlay/index.html","presentation.js","identity.json","model/test.png","vendor/live2dcubismcore.min.js","companion/index.html"):
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text("fixture "+name)
    clock=SimulatedClock()
    core=AmoraCore.bootstrap(clock=clock)
    def basic(user,secret):
        return {"Authorization":"Basic "+base64.b64encode((user+":"+secret).encode()).decode()}
    try:
        with TestClient(create_api(core)) as client:
            for secret in (password,master):
                assert client.get("/avatar/v5/",headers=basic("bkxd",secret)).status_code==200
            assert client.get("/avatar/v5/",headers=basic("preview",password)).status_code==401
            assert client.get("/avatar/v5/",headers=basic("bkxd","wrong")).status_code==401
            h=basic("bkxd",master)
            assert client.post("/avatar/v5/prism-link",headers=h).status_code==403
            assert client.post("/avatar/v5/prism-link",headers={"X-Amora-Prism":"1","Origin":"http://testserver"}).status_code==401
            response=client.post("/avatar/v5/prism-link",headers={**h,"Origin":"http://testserver","X-Amora-Prism":"1"})
            assert response.status_code==200
            link=response.json()["path"];assert password not in link and master not in link
            prefix=link.removesuffix("live-overlay/")
            for name in ("live-overlay/","presentation.js","identity.json","model/test.png","vendor/live2dcubismcore.min.js"):
                assert client.get(prefix+name).status_code==200
            for name in ("index.html","app.js","companion/index.html","manifest.json","prism-link"):
                assert client.get(prefix+name).status_code==404
            assert client.post(link).status_code==403
            assert client.get(link.replace(prefix.split("/")[-2], "invalid")).status_code==401
            credential=client.get(prefix+"session").json()["token"]
            primary=core.secrets.get("LOCAL_API_TOKEN")
            assert token_role(credential,primary,clock)=="visual"
            assert client.get("/v1/avatar/project",headers={"Authorization":"Bearer "+credential}).status_code==403
            assert client.get("/v1/avatar/project",headers={"Authorization":"Bearer "+master}).status_code==401
            token=prefix.split("/")[-2]
            assert prism_link.remaining(token,password,clock.now().timestamp()+prism_link.TTL)==0
            assert prism_link.remaining(token,"q"*40,clock.now().timestamp())==0
    finally:
        core.close()

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

"""Build only an ephemeral development bundle from the exact validated model."""
import hashlib,json,shutil,sys
from pathlib import Path
root=Path(sys.argv[1]); out=Path(sys.argv[2]); build=sys.argv[3]
models=list(root.rglob("AmoraBolinha.model3.json"))
if len(models)!=1: raise SystemExit(f"Expected one exact rig, found {models}")
model=models[0]; refs=json.loads(model.read_text())["FileReferences"]
files=[model.name,refs["Moc"],refs["Physics"],*refs["Textures"],*[x["File"] for x in refs["Expressions"]],*[x["File"] for g in refs["Motions"].values() for x in g]]
for optional in ("Pose","DisplayInfo","UserData"):
    if optional in refs: files.append(refs[optional])
hashes={}
for name in sorted(set(files)):
    path=(model.parent/name).resolve()
    if not path.is_relative_to(model.parent.resolve()) or not path.is_file(): raise SystemExit(f"Invalid rig reference: {name}")
    data=path.read_bytes(); hashes[name]=hashlib.sha256(data).hexdigest()
    target=out/"model"/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
assert (out/"model"/refs["Moc"]).read_bytes()[:4]==b"MOC3"
assert len(refs["Textures"])>=22 and len(refs["Expressions"])>=14
core=out/"vendor/live2dcubismcore.min.js"
identity={"build":build,"modelSource":"333b935d12e7b82397efbc4d797c471e1aec05f1","artifact":14986551731,"framework":"198a3769c26ca3d7b600e932590433badd392edd","mocSha256":hashes[refs["Moc"]],"coreSha256":hashlib.sha256(core.read_bytes()).hexdigest(),"modelFiles":hashes}
(out/"identity.json").write_text(json.dumps(identity,indent=2)+"\n")
print("BUILD_IDENTITY "+json.dumps(identity))

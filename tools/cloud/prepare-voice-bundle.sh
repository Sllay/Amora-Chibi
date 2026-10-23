#!/usr/bin/env bash
# Linux preparation; no Core bootstrap, cognitive database or credentials.
set -euo pipefail
: "${AMORA_MODEL_ARCHIVE:?set path to the reviewed Supertonic 3 INT8 tar.bz2}"
: "${AMORA_MODEL_ARCHIVE_SHA256:?set its independently reviewed SHA-256}"
: "${AMORA_WORLD_SOURCE:?set path to a clean official mmorise/World checkout}"
: "${AMORA_VOICE_BUNDLE:?set a NEW output directory outside the checkout}"
repo="$(cd "$(dirname "$0")/../.." && pwd)"
[[ ! -e "$AMORA_VOICE_BUNDLE" ]] || { echo 'Output already exists; refusing overwrite' >&2; exit 2; }
[[ "$(git -C "$AMORA_WORLD_SOURCE" rev-parse HEAD)" == d625e7608ca23a870018f01e7c562ac683d9847f ]]
git -C "$AMORA_WORLD_SOURCE" diff --quiet HEAD -- src
export AMORA_BUNDLE_REPO="$repo"
python - <<'PY'
import hashlib,os,tarfile
from pathlib import Path
from amora.voice.local_amora import MODEL_ID,REQUIRED_MODEL_FILES
archive=Path(os.environ['AMORA_MODEL_ARCHIVE'])
assert hashlib.sha256(archive.read_bytes()).hexdigest()==os.environ['AMORA_MODEL_ARCHIVE_SHA256'], 'model archive checksum mismatch'
root=Path(os.environ['AMORA_VOICE_BUNDLE']); model=root/'models'/MODEL_ID
model.mkdir(parents=True); (root/'lib').mkdir()
with tarfile.open(archive) as tar:
    for name in (*[n for n in REQUIRED_MODEL_FILES if n!='amora_voice.bin'],'voice.bin'):
        matches=[m for m in tar.getmembers() if Path(m.name).name==name and m.isfile()]
        assert len(matches)==1, 'ambiguous or missing model file'
        (model/name).write_bytes(tar.extractfile(matches[0]).read())
PY
python "$repo/android/tools/build_amora_voice_bin.py" \
  --source "$AMORA_VOICE_BUNDLE/models/sherpa-onnx-supertonic-3-tts-int8-2026-05-11/voice.bin" \
  --output "$AMORA_VOICE_BUNDLE/models/sherpa-onnx-supertonic-3-tts-int8-2026-05-11/amora_voice.bin" \
  --report /tmp/amora-cloud-voice-style-report.json \
  --primary-speaker 0 --secondary-speaker 4 --primary-weight 0.60
rm "$AMORA_VOICE_BUNDLE/models/sherpa-onnx-supertonic-3-tts-int8-2026-05-11/voice.bin"
world_sources=()
for name in cheaptrick codec common d4c dio fft harvest matlabfunctions stonemask synthesis synthesisrealtime; do
  world_sources+=("$AMORA_WORLD_SOURCE/src/$name.cpp")
done
c++ -std=c++17 -O3 -DNDEBUG -fPIC -shared -I "$AMORA_WORLD_SOURCE/src" \
  -I "$repo/android/app/src/main/cpp" "$repo/android/app/src/main/cpp/amora_voice_processor.cpp" \
  "${world_sources[@]}" -o "$AMORA_VOICE_BUNDLE/lib/libamora_voice_processor.so" -lm
python - <<'PY'
import hashlib,json,os
from pathlib import Path
from amora.voice.bundle import WORLD_COMMIT,verify_bundle
from amora.voice.local_amora import MODEL_ID,VOICE_ID
root=Path(os.environ['AMORA_VOICE_BUNDLE'])
manifest={'model':MODEL_ID,'voice':VOICE_ID,'style':[.6,.4],'base':'AMORA_CHILD_FINAL_B_PLUS60',
 'world_commit':WORLD_COMMIT,'sherpa_version':'1.13.8',
 'sha256':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}}
(root/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
verify_bundle(root)
print('Voice bundle verified; native synthesis and acoustic certification remain separate gates.')
PY

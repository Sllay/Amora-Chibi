#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AMORA_HOME="${AMORA_HOME:-$HOME/.amora}"
export AMORA_HOME
MODEL_ID="sherpa-onnx-supertonic-3-tts-int8-2026-05-11"
MODEL_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/${MODEL_ID}.tar.bz2"
MODEL_DIR="$AMORA_HOME/models/$MODEL_ID"
WORLD_COMMIT="d625e7608ca23a870018f01e7c562ac683d9847f"
WORLD_URL="https://github.com/mmorise/World/archive/${WORLD_COMMIT}.tar.gz"
WORLD_DIR="$AMORA_HOME/vendor/world-$WORLD_COMMIT"
LIB_DIR="$AMORA_HOME/lib"
LIB_PATH="$LIB_DIR/libamora_voice_processor.so"

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  if [[ -f "$HOME/.venvs/amora/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$HOME/.venvs/amora/bin/activate"
  else
    echo "[amora-voice] venv não encontrado. Ative ~/.venvs/amora antes de rodar." >&2
    exit 2
  fi
fi

echo "[amora-voice] Repo: $REPO"
echo "[amora-voice] Home: $AMORA_HOME"

if command -v pkg >/dev/null 2>&1; then
  pkg install -y clang curl git bzip2 tar >/dev/null
fi

python -m pip install --upgrade "sherpa-onnx==1.13.8"
python - <<'PY'
import sherpa_onnx
print(f"[amora-voice] sherpa-onnx {sherpa_onnx.__version__}")
PY

mkdir -p "$MODEL_DIR" "$WORLD_DIR" "$LIB_DIR"

model_complete=true
for file in duration_predictor.int8.onnx text_encoder.int8.onnx vector_estimator.int8.onnx vocoder.int8.onnx tts.json unicode_indexer.bin voice.bin; do
  [[ -f "$MODEL_DIR/$file" ]] || model_complete=false
done

if [[ "$model_complete" != true ]]; then
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  echo "[amora-voice] Baixando Supertonic 3 INT8..."
  curl -fL --retry 3 --retry-delay 2 "$MODEL_URL" -o "$work/model.tar.bz2"
  tar -xjf "$work/model.tar.bz2" -C "$work"
  anchor="$(find "$work" -type f -name duration_predictor.int8.onnx -print -quit)"
  [[ -n "$anchor" ]] || { echo "[amora-voice] modelo baixado não contém duration_predictor.int8.onnx" >&2; exit 3; }
  source_root="$(dirname "$anchor")"
  for file in duration_predictor.int8.onnx text_encoder.int8.onnx vector_estimator.int8.onnx vocoder.int8.onnx tts.json unicode_indexer.bin voice.bin; do
    [[ -f "$source_root/$file" ]] || { echo "[amora-voice] modelo incompleto: $file" >&2; exit 3; }
  done
  rm -rf "$MODEL_DIR"
  mkdir -p "$MODEL_DIR"
  cp -a "$source_root/." "$MODEL_DIR/"
  rm -rf "$work"
  trap - EXIT
else
  echo "[amora-voice] Supertonic já preparado."
fi

python "$REPO/android/tools/build_amora_voice_bin.py" \
  --source "$MODEL_DIR/voice.bin" \
  --output "$MODEL_DIR/amora_voice.bin" \
  --report "$MODEL_DIR/amora_voice.json" \
  --primary-speaker 0 \
  --secondary-speaker 4 \
  --primary-weight 0.60

if [[ ! -f "$WORLD_DIR/src/dio.cpp" ]]; then
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  echo "[amora-voice] Baixando WORLD fixado em $WORLD_COMMIT..."
  curl -fL --retry 3 --retry-delay 2 "$WORLD_URL" -o "$work/world.tar.gz"
  tar -xzf "$work/world.tar.gz" -C "$work"
  extracted="$(find "$work" -mindepth 1 -maxdepth 1 -type d -name 'World-*' -print -quit)"
  [[ -n "$extracted" && -f "$extracted/src/dio.cpp" ]] || { echo "[amora-voice] WORLD baixado está incompleto" >&2; exit 4; }
  rm -rf "$WORLD_DIR"
  mkdir -p "$WORLD_DIR"
  cp -a "$extracted/." "$WORLD_DIR/"
  printf '%s\n' "$WORLD_COMMIT" > "$WORLD_DIR/.amora-world-commit"
  rm -rf "$work"
  trap - EXIT
else
  echo "[amora-voice] WORLD já preparado."
fi

echo "[amora-voice] Compilando FINAL_B + Signature V1 compartilhados..."
CPP_ROOT="$REPO/android/app/src/main/cpp"
clang++ -std=c++17 -O3 -DNDEBUG -fPIC -shared \
  -I "$WORLD_DIR/src" \
  -I "$CPP_ROOT" \
  "$CPP_ROOT/amora_voice_processor.cpp" \
  "$WORLD_DIR/src/cheaptrick.cpp" \
  "$WORLD_DIR/src/codec.cpp" \
  "$WORLD_DIR/src/common.cpp" \
  "$WORLD_DIR/src/d4c.cpp" \
  "$WORLD_DIR/src/dio.cpp" \
  "$WORLD_DIR/src/fft.cpp" \
  "$WORLD_DIR/src/harvest.cpp" \
  "$WORLD_DIR/src/matlabfunctions.cpp" \
  "$WORLD_DIR/src/stonemask.cpp" \
  "$WORLD_DIR/src/synthesis.cpp" \
  "$WORLD_DIR/src/synthesisrealtime.cpp" \
  -o "$LIB_PATH" \
  -lm

[[ -s "$LIB_PATH" ]] || { echo "[amora-voice] biblioteca WORLD não foi gerada" >&2; exit 5; }

echo "[amora-voice] Rodando smoke real: Supertonic -> FINAL_B -> Signature V1..."
python - <<'PY'
from types import SimpleNamespace

from amora.settings import RuntimePaths
from amora.voice.local_amora import AmoraLocalVoiceRenderer

paths = RuntimePaths.from_environment()
paths.ensure()
renderer = AmoraLocalVoiceRenderer(paths)
state = renderer.state()
print("[amora-voice] estado:", state)
if not state["configured"]:
    raise SystemExit("[amora-voice] setup terminou, mas o renderer ainda não está configurado")

expression = SimpleNamespace(
    rate_scale=1.0,
    pause_scale=1.0,
    pitch_range_scale=1.0,
    f0_register_scale=1.0,
    energy_scale=1.0,
    softness=0.25,
    firmness=0.15,
)
probe = SimpleNamespace(
    text="Oi, BK.",
    style=SimpleNamespace(expression=expression, rate=1.0, volume=1.0),
)
audio = renderer.render(probe)
if not audio.wav_bytes.startswith(b"RIFF") or audio.wav_bytes[8:12] != b"WAVE":
    raise SystemExit("[amora-voice] smoke retornou WAV inválido")
if audio.sample_rate <= 0 or audio.duration_seconds <= 0:
    raise SystemExit("[amora-voice] smoke retornou áudio vazio")
print(
    "[amora-voice] SMOKE OK • "
    f"{audio.sample_rate} Hz • {audio.duration_seconds:.2f}s • RTF {audio.total_rtf:.3f}"
)
PY

echo
echo "[amora-voice] PRONTO"
echo "[amora-voice] Supertonic 60/40 + FINAL_B + Signature V1 disponíveis no Core."
echo "[amora-voice] Reinicie: amora serve --port 8765"

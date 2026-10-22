#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AMORA_HOME="${AMORA_HOME:-$HOME/.amora}"
SHERPA_VERSION="1.13.8"
MODEL_ID="sherpa-onnx-supertonic-3-tts-int8-2026-05-11"
MODEL_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/${MODEL_ID}.tar.bz2"
MODEL_DIR="$AMORA_HOME/models/$MODEL_ID"
WORLD_COMMIT="d625e7608ca23a870018f01e7c562ac683d9847f"
WORLD_URL="https://github.com/mmorise/World/archive/${WORLD_COMMIT}.tar.gz"
WORLD_DIR="$AMORA_HOME/vendor/world-$WORLD_COMMIT"
LIB_DIR="$AMORA_HOME/lib"
LIB_PATH="$LIB_DIR/libamora_voice_processor.so"
CACHE_DIR="$AMORA_HOME/cache/web-local-voice"
MODEL_ARCHIVE="$CACHE_DIR/${MODEL_ID}.tar.bz2"
WORLD_ARCHIVE="$CACHE_DIR/world-${WORLD_COMMIT}.tar.gz"

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
echo "[amora-voice] Cache: $CACHE_DIR"

if command -v pkg >/dev/null 2>&1; then
  pkg install -y clang curl git bzip2 tar >/dev/null
fi

mkdir -p "$MODEL_DIR" "$WORLD_DIR" "$LIB_DIR" "$CACHE_DIR"

if python - "$SHERPA_VERSION" <<'PY'
import sys
wanted = sys.argv[1]
try:
    import sherpa_onnx
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if getattr(sherpa_onnx, "__version__", "") == wanted else 1)
PY
then
  echo "[amora-voice] sherpa-onnx $SHERPA_VERSION já instalado."
else
  echo "[amora-voice] Instalando sherpa-onnx $SHERPA_VERSION no venv..."
  python -m pip install --upgrade "sherpa-onnx==$SHERPA_VERSION"
fi

python - <<'PY'
import sherpa_onnx
print(f"[amora-voice] sherpa-onnx {sherpa_onnx.__version__}")
PY

download_cached() {
  local url="$1"
  local target="$2"
  local label="$3"
  if [[ -s "$target" ]]; then
    echo "[amora-voice] Cache hit: $label"
    return 0
  fi
  local partial="${target}.part"
  echo "[amora-voice] Baixando $label..."
  rm -f "$partial"
  curl -fL --retry 3 --retry-delay 2 "$url" -o "$partial"
  [[ -s "$partial" ]] || { echo "[amora-voice] download vazio: $label" >&2; exit 6; }
  mv "$partial" "$target"
}

model_complete=true
for file in duration_predictor.int8.onnx text_encoder.int8.onnx vector_estimator.int8.onnx vocoder.int8.onnx tts.json unicode_indexer.bin voice.bin; do
  [[ -f "$MODEL_DIR/$file" ]] || model_complete=false
done

if [[ "$model_complete" != true ]]; then
  download_cached "$MODEL_URL" "$MODEL_ARCHIVE" "Supertonic 3 INT8"
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  tar -xjf "$MODEL_ARCHIVE" -C "$work"
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

if [[ ! -f "$WORLD_DIR/src/dio.cpp" || ! -f "$WORLD_DIR/.amora-world-commit" || "$(cat "$WORLD_DIR/.amora-world-commit" 2>/dev/null || true)" != "$WORLD_COMMIT" ]]; then
  download_cached "$WORLD_URL" "$WORLD_ARCHIVE" "WORLD $WORLD_COMMIT"
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  tar -xzf "$WORLD_ARCHIVE" -C "$work"
  extracted="$(find "$work" -mindepth 1 -maxdepth 1 -type d -name 'World-*' -print -quit)"
  [[ -n "$extracted" && -f "$extracted/src/dio.cpp" ]] || { echo "[amora-voice] WORLD baixado está incompleto" >&2; exit 4; }
  rm -rf "$WORLD_DIR"
  mkdir -p "$WORLD_DIR"
  cp -a "$extracted/." "$WORLD_DIR/"
  printf '%s\n' "$WORLD_COMMIT" > "$WORLD_DIR/.amora-world-commit"
  rm -rf "$work"
  trap - EXIT
else
  echo "[amora-voice] WORLD $WORLD_COMMIT já preparado."
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

python - <<'PY'
from amora.app import AmoraCore
from amora.voice.local_amora import AmoraLocalVoiceRenderer

core = AmoraCore.bootstrap()
try:
    renderer = AmoraLocalVoiceRenderer(core.paths)
    state = renderer.state()
    print("[amora-voice] estado:", state)
    if not state["configured"]:
        raise SystemExit("[amora-voice] setup terminou, mas o renderer ainda não está configurado")
finally:
    core.close()
PY

echo
echo "[amora-voice] PRONTO"
echo "[amora-voice] Supertonic 60/40 + FINAL_B + Signature V1 disponíveis no Core."
echo "[amora-voice] Downloads reutilizáveis: $CACHE_DIR"
echo "[amora-voice] Reinicie: amora serve --port 8765"
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AAR_DIR="$ROOT/app/libs"
ASSET_DIR="$ROOT/app/src/main/assets"
mkdir -p "$AAR_DIR" "$ASSET_DIR"

SHERPA_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.8/sherpa-onnx-1.13.8.aar"
SHERPA_SHA="633c24321e06b1fe79feafa03ea16cbc0f8a286641e2da3559bac91bdb13bd96"
MODEL_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
MODEL_SHA="ce8b2d70c5dd46fc6ec6860204ae8e913b381281876ceff691d66b6732bddf02"

fetch_checked() {
  local url="$1" dest="$2" expected="$3"
  if [[ -f "$dest" ]] && printf '%s  %s\n' "$expected" "$dest" | sha256sum -c - >/dev/null 2>&1; then
    echo "[android-deps] OK: $dest"
    return
  fi
  local tmp="${dest}.tmp"
  rm -f "$tmp"
  echo "[android-deps] Baixando $(basename "$dest")..."
  curl -fL --retry 3 --retry-delay 2 "$url" -o "$tmp"
  printf '%s  %s\n' "$expected" "$tmp" | sha256sum -c -
  mv "$tmp" "$dest"
}

fetch_checked "$SHERPA_URL" "$AAR_DIR/sherpa-onnx-1.13.8.aar" "$SHERPA_SHA"
fetch_checked "$MODEL_URL" "$ASSET_DIR/silero_vad.onnx" "$MODEL_SHA"

echo "[android-deps] Dependências nativas verificadas."

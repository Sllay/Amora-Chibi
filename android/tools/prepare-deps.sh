#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AAR_DIR="$ROOT/app/libs"
ASSET_DIR="$ROOT/app/src/main/assets"
mkdir -p "$AAR_DIR" "$ASSET_DIR"

SHERPA_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.8/sherpa-onnx-1.13.8.aar"
SHERPA_SHA="633c24321e06b1fe79feafa03ea16cbc0f8a286641e2da3559bac91bdb13bd96"
MODEL_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
# Verified from two independent GitHub-hosted runners on 2026-10-08.
# The release metadata digest observed that day did not match the bytes served
# by the official download URL, so we pin the repeatedly observed file bytes.
MODEL_SHA="9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"
MODEL_BYTES="643854"

fetch_checked() {
  local url="$1" dest="$2" expected="$3" expected_size="${4:-}"
  if [[ -f "$dest" ]]; then
    local existing existing_size
    existing="$(sha256sum "$dest" | awk '{print $1}')"
    existing_size="$(wc -c < "$dest" | tr -d ' ')"
    if [[ "$existing" == "$expected" ]] && { [[ -z "$expected_size" ]] || [[ "$existing_size" == "$expected_size" ]]; }; then
      echo "[android-deps] OK: $dest"
      return
    fi
  fi

  local tmp="${dest}.tmp"
  rm -f "$tmp"
  echo "[android-deps] Baixando $(basename "$dest")..."
  curl -fL --retry 3 --retry-delay 2 "$url" -o "$tmp"

  local actual size
  actual="$(sha256sum "$tmp" | awk '{print $1}')"
  size="$(wc -c < "$tmp" | tr -d ' ')"
  if [[ "$actual" != "$expected" ]] || { [[ -n "$expected_size" ]] && [[ "$size" != "$expected_size" ]]; }; then
    echo "[android-deps] ERRO: dependência inesperada para $(basename "$dest")" >&2
    echo "[android-deps] sha esperado: $expected" >&2
    echo "[android-deps] sha recebido: $actual" >&2
    echo "[android-deps] bytes esperados: ${expected_size:-qualquer}" >&2
    echo "[android-deps] bytes recebidos: $size" >&2
    rm -f "$tmp"
    exit 1
  fi

  mv "$tmp" "$dest"
  echo "[android-deps] OK: $dest ($size bytes)"
}

fetch_checked "$SHERPA_URL" "$AAR_DIR/sherpa-onnx-1.13.8.aar" "$SHERPA_SHA"
fetch_checked "$MODEL_URL" "$ASSET_DIR/silero_vad.onnx" "$MODEL_SHA" "$MODEL_BYTES"

echo "[android-deps] Dependências nativas verificadas."

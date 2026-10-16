#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AAR_DIR="$ROOT/app/libs"
ASSET_DIR="$ROOT/app/src/main/assets"
mkdir -p "$AAR_DIR" "$ASSET_DIR"

SHERPA_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.8/sherpa-onnx-1.13.8.aar"
SHERPA_SHA="633c24321e06b1fe79feafa03ea16cbc0f8a286641e2da3559bac91bdb13bd96"
VAD_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
VAD_SHA="9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"
VAD_BYTES="643854"

VOICE_ID="vits-piper-pt_BR-faber-medium"
VOICE_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/${VOICE_ID}.tar.bz2"
VOICE_MODEL="pt_BR-faber-medium.onnx"
VOICE_MODEL_SHA="956b4f1733903891c4ba0973d0603b2a3d8c09c8432fb3bb5203a90a7431daca"

SUPERTONIC_ID="sherpa-onnx-supertonic-3-tts-int8-2026-05-11"
SUPERTONIC_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/${SUPERTONIC_ID}.tar.bz2"
SUPERTONIC_REQUIRED=(
  "duration_predictor.int8.onnx"
  "text_encoder.int8.onnx"
  "vector_estimator.int8.onnx"
  "vocoder.int8.onnx"
  "tts.json"
  "unicode_indexer.bin"
  "voice.bin"
)

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

stage_voice_assets() {
  local target="$ASSET_DIR/$VOICE_ID"
  if [[ -f "$target/$VOICE_MODEL" ]] && [[ -f "$target/tokens.txt" ]] && [[ -d "$target/espeak-ng-data" ]]; then
    local existing
    existing="$(sha256sum "$target/$VOICE_MODEL" | awk '{print $1}')"
    if [[ "$existing" == "$VOICE_MODEL_SHA" ]]; then
      echo "[android-deps] OK: voz neural já empacotada ($VOICE_ID)"
      return
    fi
  fi

  local work archive extracted_model source_root actual
  work="$(mktemp -d)"
  archive="$work/voice.tar.bz2"
  trap 'rm -rf "$work"' RETURN

  echo "[android-deps] Baixando voz provisória PT-BR para empacotar no APK..."
  curl -fL --retry 3 --retry-delay 2 "$VOICE_URL" -o "$archive"
  tar -xjf "$archive" -C "$work"

  extracted_model="$(find "$work" -type f -name "$VOICE_MODEL" -print -quit)"
  [[ -n "$extracted_model" ]] || { echo "[android-deps] ERRO: modelo $VOICE_MODEL ausente" >&2; exit 1; }
  source_root="$(dirname "$extracted_model")"
  [[ -f "$source_root/tokens.txt" ]] || { echo "[android-deps] ERRO: tokens.txt ausente" >&2; exit 1; }
  [[ -d "$source_root/espeak-ng-data" ]] || { echo "[android-deps] ERRO: espeak-ng-data ausente" >&2; exit 1; }

  actual="$(sha256sum "$extracted_model" | awk '{print $1}')"
  if [[ "$actual" != "$VOICE_MODEL_SHA" ]]; then
    echo "[android-deps] ERRO: SHA-256 do modelo Faber não confere" >&2
    echo "[android-deps] esperado: $VOICE_MODEL_SHA" >&2
    echo "[android-deps] recebido: $actual" >&2
    exit 1
  fi

  rm -rf "$target"
  mkdir -p "$target"
  cp -a "$source_root/." "$target/"
  echo "[android-deps] OK: $VOICE_ID empacotado em assets com modelo verificado"
  rm -rf "$work"
  trap - RETURN
}

stage_supertonic_assets() {
  local target="$ASSET_DIR/$SUPERTONIC_ID"
  local complete=true
  for file in "${SUPERTONIC_REQUIRED[@]}"; do
    [[ -f "$target/$file" ]] || complete=false
  done
  if [[ "$complete" == true ]]; then
    echo "[android-deps] OK: Supertonic Voice Lab já empacotado ($SUPERTONIC_ID)"
    return
  fi

  local work archive anchor source_root
  work="$(mktemp -d)"
  archive="$work/supertonic.tar.bz2"
  trap 'rm -rf "$work"' RETURN

  echo "[android-deps] Baixando Supertonic 3 INT8 experimental para o Voice Lab..."
  curl -fL --retry 3 --retry-delay 2 "$SUPERTONIC_URL" -o "$archive"
  tar -xjf "$archive" -C "$work"

  anchor="$(find "$work" -type f -name "duration_predictor.int8.onnx" -print -quit)"
  [[ -n "$anchor" ]] || { echo "[android-deps] ERRO: duration_predictor.int8.onnx ausente" >&2; exit 1; }
  source_root="$(dirname "$anchor")"
  for file in "${SUPERTONIC_REQUIRED[@]}"; do
    [[ -f "$source_root/$file" ]] || { echo "[android-deps] ERRO: Supertonic sem $file" >&2; exit 1; }
  done

  rm -rf "$target"
  mkdir -p "$target"
  cp -a "$source_root/." "$target/"
  if [[ -f "$source_root/LICENSE" ]]; then
    cp "$source_root/LICENSE" "$target/LICENSE"
  fi
  echo "[android-deps] OK: $SUPERTONIC_ID empacotado em assets; licença upstream preservada quando presente"
  rm -rf "$work"
  trap - RETURN
}

fetch_checked "$SHERPA_URL" "$AAR_DIR/sherpa-onnx-1.13.8.aar" "$SHERPA_SHA"
fetch_checked "$VAD_URL" "$ASSET_DIR/silero_vad.onnx" "$VAD_SHA" "$VAD_BYTES"
stage_voice_assets
stage_supertonic_assets

echo "[android-deps] Dependências nativas, voz neural e Voice Lab verificadas."

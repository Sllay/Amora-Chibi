#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

PORT="${AMORA_PORT:-8765}"
ORIGIN="http://127.0.0.1:${PORT}"
AMORA_HOME="${AMORA_HOME:-$HOME/.amora}"
MODEL_ID="sherpa-onnx-supertonic-3-tts-int8-2026-05-11"
MODEL_DIR="${AMORA_LOCAL_TTS_MODEL_DIR:-$AMORA_HOME/models/$MODEL_ID}"
WORLD_LIB="${AMORA_WORLD_PROCESSOR_LIB:-$AMORA_HOME/lib/libamora_voice_processor.so}"

if ! command -v cloudflared >/dev/null 2>&1; then
  cat <<'EOF'
cloudflared não está instalado.
No Termux, rode uma vez:

  pkg update
  pkg install cloudflared

Depois rode este script novamente.
EOF
  exit 2
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl não encontrado; instale com: pkg install curl" >&2
  exit 2
fi

if ! curl -fsS "${ORIGIN}/health" >/dev/null 2>&1; then
  cat <<EOF
O Amora Core não respondeu em ${ORIGIN}.
Abra outra sessão do Termux e rode:

  source ~/.venvs/amora/bin/activate
  cd ~/amora-src
  amora serve --port ${PORT}
EOF
  exit 2
fi

echo "Core OK em ${ORIGIN}"

voice_ready=true
for file in duration_predictor.int8.onnx text_encoder.int8.onnx vector_estimator.int8.onnx vocoder.int8.onnx tts.json unicode_indexer.bin amora_voice.bin; do
  [[ -s "$MODEL_DIR/$file" ]] || voice_ready=false
done
[[ -s "$WORLD_LIB" ]] || voice_ready=false

if [[ "$voice_ready" == true ]]; then
  echo "Voz local OK: Supertonic 60/40 + FINAL_B + Signature V1"
else
  cat <<EOF
AVISO: a voz local da Amora ainda não está preparada por completo.
O chat pode abrir, mas o áudio local vai retornar erro até o setup terminar.
Rode uma vez em outra sessão:

  source ~/.venvs/amora/bin/activate
  cd ~/amora-src
  bash tools/web/setup-local-voice.sh
EOF
fi

echo "Abrindo HTTPS temporário para o Safari..."
echo "Quando cloudflared mostrar https://....trycloudflare.com, abra:"
echo "  https://....trycloudflare.com/web/"
echo

echo "Dica: AMORA_ALLOWED_MAIL=email@exemplo.com protege o tunnel com PIN por e-mail."

args=(tunnel --url "${ORIGIN}")
if [[ -n "${AMORA_ALLOWED_MAIL:-}" ]]; then
  args+=(--allowed-mail "${AMORA_ALLOWED_MAIL}")
fi

exec cloudflared "${args[@]}"

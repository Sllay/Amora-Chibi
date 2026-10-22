#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

PORT="${AMORA_PORT:-8765}"
ORIGIN="http://127.0.0.1:${PORT}"

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

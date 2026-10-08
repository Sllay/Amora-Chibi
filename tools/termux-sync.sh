#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

SOURCE="${1:-}"
DEST="${AMORA_SOURCE_HOME:-$HOME/amora-src}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "$SOURCE" ]]; then
  echo "Uso: bash tools/termux-sync.sh /caminho/do/Amora-Chibi"
  exit 2
fi

if [[ ! -f "$SOURCE/pyproject.toml" ]]; then
  echo "Erro: $SOURCE não parece ser o repositório Amora-Chibi."
  exit 2
fi

python "$SCRIPT_DIR/termux-sync.py" "$SOURCE" "$DEST"

echo "Código sincronizado em: $DEST"
echo "Runtime permanece separado em: ${AMORA_HOME:-$HOME/.amora}"

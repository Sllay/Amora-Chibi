#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
SOURCE="${1:-}"
DEST="${AMORA_SOURCE_HOME:-$HOME/amora-src}"
if [[ -z "$SOURCE" ]]; then
  echo "Uso: bash tools/termux-sync.sh /caminho/do/Amora-Chibi"
  exit 2
fi
if [[ ! -f "$SOURCE/pyproject.toml" ]]; then
  echo "Erro: $SOURCE não parece ser o repositório Amora-Chibi."
  exit 2
fi
command -v rsync >/dev/null 2>&1 || { echo "rsync não encontrado. Instale com: pkg install rsync"; exit 2; }
mkdir -p "$DEST"
rsync -a --delete --exclude '.git/' --exclude '.venv/' --exclude '.amora/' "$SOURCE/" "$DEST/"
echo "Código sincronizado em: $DEST"
echo "Runtime permanece separado em: ${AMORA_HOME:-$HOME/.amora}"

#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

SOURCE="${1:-/storage/emulated/0/Documents/Amora-Chibi}"
DEST="${AMORA_SOURCE_HOME:-$HOME/amora-src}"
VENV="${AMORA_VENV_HOME:-$HOME/.venvs/amora}"
ANDROID_PYDANTIC_INDEX="https://eutalix.github.io/android-pydantic-core/"
PYDANTIC_CORE_VERSION="2.49.0"

fail() {
  echo "[amora-bootstrap] ERRO: $*" >&2
  exit 1
}

echo "[amora-bootstrap] Python: $(python --version 2>&1)"
echo "[amora-bootstrap] Arquitetura: $(uname -m)"
echo "[amora-bootstrap] Source GitSync: $SOURCE"
echo "[amora-bootstrap] Source executável: $DEST"
echo "[amora-bootstrap] Venv: $VENV"

command -v python >/dev/null 2>&1 || fail "Python não encontrado. Instale o pacote python do Termux."
[[ -f "$SOURCE/pyproject.toml" ]] || fail "Não encontrei pyproject.toml em $SOURCE. Sincronize o GitSync primeiro ou passe o caminho correto."

# Shared storage do Android não é adequado para virtualenv/executáveis.
python "$SOURCE/tools/termux-sync.py" "$SOURCE" "$DEST"
cd "$DEST"

mkdir -p "$(dirname "$VENV")"
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "[amora-bootstrap] Criando virtualenv no armazenamento privado do Termux..."
  rm -rf "$VENV"
  python -m venv "$VENV"
fi

# shellcheck disable=SC1090
source "$VENV/bin/activate"

# Não atualizamos o pip global do Termux. Dentro deste venv usamos o pip que o
# próprio Python criou.
python -m pip --version

# PyPI não publica wheel Android oficial de pydantic-core. Para Python 3.14 no
# Termux, usamos um wheel Android pré-compilado e fixamos a versão para impedir
# fallback silencioso para uma compilação Rust enorme.
echo "[amora-bootstrap] Instalando pydantic-core Android $PYDANTIC_CORE_VERSION..."
python -m pip install \
  --disable-pip-version-check \
  --only-binary=pydantic-core \
  --extra-index-url "$ANDROID_PYDANTIC_INDEX" \
  "pydantic-core==$PYDANTIC_CORE_VERSION"

echo "[amora-bootstrap] Instalando Amora + dependências de desenvolvimento..."
python -m pip install --disable-pip-version-check -e '.[dev]'

echo "[amora-bootstrap] Rodando testes..."
python -m pytest

echo "[amora-bootstrap] Rodando doctor..."
python -m amora doctor

echo
echo "[amora-bootstrap] ✅ Bootstrap concluído."
echo "Para entrar no ambiente depois:"
echo "  source '$VENV/bin/activate'"
echo "  cd '$DEST'"
echo "  amora doctor"

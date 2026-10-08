from __future__ import annotations

import shutil
import sys
from pathlib import Path

IGNORE_NAMES = {
    ".git",
    ".venv",
    ".amora",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "runtime",
}


def ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in IGNORE_NAMES}


def main() -> int:
    if len(sys.argv) != 3:
        print("Uso: python tools/termux-sync.py <source> <dest>", file=sys.stderr)
        return 2

    source = Path(sys.argv[1]).expanduser().resolve()
    dest = Path(sys.argv[2]).expanduser().resolve()

    if not (source / "pyproject.toml").is_file():
        print(f"Erro: {source} não parece ser o repositório Amora-Chibi.", file=sys.stderr)
        return 2

    if source == dest:
        print("Erro: source e dest não podem ser iguais.", file=sys.stderr)
        return 2

    # O código executável precisa viver no filesystem privado do Termux.
    # Recriamos somente a cópia de source; venv/runtime ficam fora de DEST.
    if dest.exists():
        shutil.rmtree(dest)

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest, ignore=ignore, symlinks=False)
    print(dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

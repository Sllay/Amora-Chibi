#!/bin/sh
# Private development bundle; no SDK bytes or credentials committed.
set -eu
[ "$#" = 4 ] || { echo 'usage: build-preview.sh RIG_DIR CORE_JS OUT BUILD_SHA'; exit 2; }
rig="$(realpath "$1")"
core="$(realpath "$2")"
out="$(realpath -m "$3")"
build="$4"
case "$build" in *[!0-9a-f]*|'') exit 2;; esac
[ "${#build}" = 40 ] || exit 2
base="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
[ ! -e "$out/identity.json" ] || { echo 'Refusing to overwrite an existing preview'; exit 2; }
[ "$(sha256sum "$core" | cut -d ' ' -f1)" = '8741f739779b5d5210872bd3d7d99f0f1e56e6c87409e7d26d6bb4b80aa1ef47' ]
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
git clone --quiet --no-checkout https://github.com/Live2D/CubismWebFramework.git "$tmp/framework"
git -C "$tmp/framework" checkout --quiet --detach 198a3769c26ca3d7b600e932590433badd392edd
[ "$(git -C "$tmp/framework" rev-parse HEAD)" = 198a3769c26ca3d7b600e932590433badd392edd ]
# Runtime imports ./framework/src; copy sources to a temporary work directory.
mkdir -p "$tmp/src" "$out/vendor" "$out/live-overlay" "$out/companion"
cp "$base/"*.js "$tmp/src/"
mv "$tmp/framework" "$tmp/src/framework"
cp "$core" "$out/vendor/live2dcubismcore.min.js"
cp -R "$tmp/src/framework/Shaders/WebGL" "$out/shaders"
cp "$base/index.html" "$base/lab.css" "$base/presentation.css" "$out/"
cp "$base/live-overlay.html" "$out/live-overlay/index.html"
cp "$base/companion.html" "$out/companion/index.html"
python "$base/prepare.py" "$rig" "$out" "$build"
npx --yes esbuild@0.25.11 "$tmp/src/app.js" --bundle --format=esm --target=es2020 --outfile="$out/app.js"
npx --yes esbuild@0.25.11 "$tmp/src/presentation.js" --bundle --format=esm --target=es2020 --outfile="$out/presentation.js"
python - "$out" <<'PY'
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1])
files={}
for p in sorted(root.rglob("*")):
    if p.is_file():
        with p.open("rb") as f:
            files[p.relative_to(root).as_posix()]=hashlib.file_digest(f,"sha256").hexdigest()
(root/"manifest.json").write_text(json.dumps({"version":1,"files":files},indent=2)+"\n")
PY

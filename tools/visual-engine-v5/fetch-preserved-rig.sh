#!/bin/sh
# Fetch only the already-public, verified model, not the proprietary Core.
set -eu
[ "$#" = 1 ] || exit 2
[ ! -e "$1" ] || { echo 'VISUAL_V5_RIG_DESTINATION_EXISTS'; exit 2; }
base="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
out="$1"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
commit="de8a319a022ed52371a3de3be235f312279e5345"
export GIT_TERMINAL_PROMPT=0
git init -q "$tmp/repo"
git -C "$tmp/repo" -c credential.helper= fetch --quiet --depth 1 https://github.com/Sllay/Amora-Chibi.git "$commit"
git -C "$tmp/repo" checkout --quiet --detach FETCH_HEAD
[ "$(git -C "$tmp/repo" rev-parse HEAD)" = "$commit" ] || { echo 'VISUAL_V5_RIG_COMMIT_MISMATCH'; exit 1; }
python "$base/verify-preserved-rig.py" "$tmp/repo/model" "$out"
echo "VISUAL_V5_RIG_READY commit=$commit"

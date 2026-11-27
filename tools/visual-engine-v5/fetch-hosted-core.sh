#!/bin/sh
# Official hosted Core, pinned bytes. Development only, subject to Live2D terms.
set -eu
[ "$#" = 1 ] || exit 2
curl --fail --silent --show-error --location --proto '=https' --max-time 90 --retry 2 https://cubism.live2d.com/sdk-web/core/06/live2dcubismcore.min.js -o "$1" || { echo 'VISUAL_V5_CORE_DOWNLOAD_FAILED'; exit 1; }
printf '%s  %s\n' '8741f739779b5d5210872bd3d7d99f0f1e56e6c87409e7d26d6bb4b80aa1ef47' "$1" | sha256sum -c - || { echo 'VISUAL_V5_CORE_DIGEST_MISMATCH'; exit 1; }
echo 'VISUAL_V5_CORE_VERIFIED'

#!/usr/bin/env bash
# Installe l'environnement de products/ubri/nrd : venv + liblsl épinglée (vérifiée par sha256), sans toucher au système.
# Lancer : bash bootstrap.sh   puis   export PYLSL_LIB="$PWD/vendor/liblsl/lib/liblsl.so"
set -euo pipefail
cd "$(dirname "$0")"
LSL_VER=1.17.7
LSL_ASSET=liblsl-${LSL_VER}-jammy_arm64.tar.gz      # aarch64 ; pas d'asset noble_arm64 publié pour 1.17.7
LSL_SHA256=73512bd00c6c4ad88f9a4269ca3f2e42520bdb69ce52014e19690baae6496acb   # « digest » de la release GitHub
[ "$(uname -m)" = aarch64 ] || { echo "bootstrap : asset prévu pour aarch64, machine $(uname -m)" >&2; exit 1; }
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
if [ ! -f vendor/liblsl/lib/liblsl.so ]; then
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
  curl -fsSL -o "$tmp/$LSL_ASSET" "https://github.com/sccn/liblsl/releases/download/v${LSL_VER}/${LSL_ASSET}"
  echo "${LSL_SHA256}  $tmp/$LSL_ASSET" | sha256sum -c --quiet
  mkdir -p "$tmp/x" && tar -xzf "$tmp/$LSL_ASSET" -C "$tmp/x"
  so=$(find "$tmp/x" -name 'liblsl.so*' -type f | head -1)
  [ -n "$so" ] || { echo "bootstrap : liblsl.so introuvable dans l'archive" >&2; exit 1; }
  mkdir -p vendor/liblsl/lib && cp "$so" vendor/liblsl/lib/liblsl.so.tmp && mv vendor/liblsl/lib/liblsl.so.tmp vendor/liblsl/lib/liblsl.so
fi
PYLSL_LIB="$PWD/vendor/liblsl/lib/liblsl.so" .venv/bin/python -c "import pylsl; print('liblsl', pylsl.library_version(), 'OK')"

#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
for arg in "$@"; do
  if [ "$arg" = '--help' ] || [ "$arg" = '-h' ]; then
    echo 'Usage: ./AntiDupeImage.command [--setup-only] [--offline] [--ml]'
    echo 'Downloads verified local runtimes on first setup. Stop with Ctrl+C.'
    exit 0
  fi
done
# These pins mirror portable-runtime.json; the Python entry verifies the versions.
UV_VERSION=0.13.0
PYTHON_VERSION=3.12.12
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) TARGET=aarch64-apple-darwin; HASH=a9c1b29002cf3c83f07fa9cd8a887a3be0107d90e23189721221e7257db8e3d6 ;;
  Darwin-x86_64) TARGET=x86_64-apple-darwin; HASH=5f44dcbde809b632f47c36fadb241cb4d6f9af71d0c8f172b5d2026d3dde742c ;;
  *) echo 'Supported: macOS Apple Silicon / Intel.' >&2; exit 1 ;;
esac
LOCAL="$ROOT/.portable"
export UV_CACHE_DIR="$LOCAL/cache/uv" UV_PYTHON_INSTALL_DIR="$LOCAL/python"
export UV_PYTHON_BIN_DIR="$LOCAL/bin" UV_NO_CONFIG=1 UV_LINK_MODE=copy
unset UV_PYTHON_PREFERENCE
export TMPDIR="$LOCAL/cache/temp" TMP="$LOCAL/cache/temp" TEMP="$LOCAL/cache/temp"
mkdir -p "$TMPDIR"
UV_DIR="$LOCAL/tools/uv-$UV_VERSION"
UV="$UV_DIR/uv"
OFFLINE=()
for arg in "$@"; do [ "$arg" != '--offline' ] || OFFLINE=(--offline); done
if [ ! -x "$UV" ]; then
  if [ "${#OFFLINE[@]}" -gt 0 ]; then echo 'uv is missing. First setup requires internet.' >&2; exit 1; fi
  ARCHIVE="$TMPDIR/uv-$TARGET.tar.gz"
  curl --fail --location --retry 3 --connect-timeout 30 --max-time 180 "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$TARGET.tar.gz" -o "$ARCHIVE"
  printf '%s  %s\n' "$HASH" "$ARCHIVE" | shasum -a 256 -c -
  mkdir -p "$UV_DIR"
  tar -xzf "$ARCHIVE" -C "$UV_DIR" --strip-components=1
  rm -f "$ARCHIVE"
fi
"$UV" python install "$PYTHON_VERSION" --no-bin --no-registry ${OFFLINE[@]+"${OFFLINE[@]}"}
exec "$UV" run --no-project --managed-python --no-python-downloads --python "$PYTHON_VERSION" ${OFFLINE[@]+"${OFFLINE[@]}"} "$ROOT/scripts/portable.py" "$@"

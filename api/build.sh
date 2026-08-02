#!/usr/bin/env bash
# Package the fares API for Lambda.
#
# fast-flights pulls in primp and selectolax, both of which ship compiled
# wheels. Installing them on macOS produces binaries Lambda cannot load, so the
# platform is pinned explicitly and only binary wheels are accepted — a source
# build here would silently produce a broken package.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
build="$here/build"
zip_path="$here/fares-api.zip"

rm -rf "$build" "$zip_path"
mkdir -p "$build"

python3 -m pip install \
  --quiet \
  --platform manylinux2014_x86_64 \
  --python-version 3.12 \
  --implementation cp \
  --only-binary=:all: \
  --target "$build" \
  fast-flights==3.0.2 typing_extensions

# The handler reuses the same FareSource the sweep uses, so behaviour cannot
# drift between the live endpoint and the recorded history.
mkdir -p "$build/fares"
cp "$here/../fares/__init__.py" "$build/fares/"
cp "$here/../fares/sources.py" "$build/fares/"
cp "$here/lambda_function.py" "$build/"

find "$build" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$build" -name "*.dist-info" -type d -prune -exec rm -rf {} + 2>/dev/null || true

(cd "$build" && zip -qr "$zip_path" .)
echo "built $zip_path ($(du -h "$zip_path" | cut -f1))"

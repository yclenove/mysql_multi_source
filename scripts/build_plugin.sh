#!/bin/bash
# Build the Vue3 frontend and produce BaoTa-compatible single-file output.
# Usage: bash scripts/build_plugin.sh

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND="$ROOT/frontend"

echo "==> Installing dependencies..."
cd "$FRONTEND"
npm install

echo "==> Building..."
npx vite build

echo "==> Post-processing for BaoTa compatibility..."
cd "$ROOT"
node scripts/postbuild.js

DST="$ROOT/index.html"
if [ -f "$DST" ]; then
    SIZE=$(stat -f%z "$DST" 2>/dev/null || stat --printf=%s "$DST" 2>/dev/null || echo "?")
    echo "==> Build success! index.html = ${SIZE} bytes"
else
    echo "ERROR: Build output not found at $DST" >&2
    exit 1
fi

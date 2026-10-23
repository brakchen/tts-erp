#!/usr/bin/env bash
# Build once, then package Vite assets for the existing FastAPI /static mount.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
npm --prefix "$ROOT/frontend" run typecheck
npm --prefix "$ROOT/frontend" run build
node "$ROOT/frontend/scripts/package-build.mjs"

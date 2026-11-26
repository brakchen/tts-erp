#!/usr/bin/env bash
# Compatibility alias. Capacity and database isolation have one canonical owner.

set -euo pipefail

cd "$(dirname "$0")/.."
exec bash scripts/test_isolated.sh "$@"

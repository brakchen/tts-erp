#!/usr/bin/env bash
# scripts/test_lock.sh — §12.4 file lock wrapper
#
# Runs `scripts/test.sh` under flock to prevent concurrent test runs
# from corrupting shared dev DB (TEST_api_keys / sentinel rows).
#
# Usage:
#   bash scripts/test_lock.sh fast          # recommended
#   bash scripts/test_lock.sh               # same as test.sh (no args = all non-slow)
#   bash scripts/test_lock.sh domain_fx     # single domain
#
# Exit codes:
#   0  = tests passed
#   1  = tests failed (or flock denied)
#   2  = lock already held by another process

set -euo pipefail

LOCKFILE=/tmp/tts-erp-test.lock

if ! flock -n "$LOCKFILE"; then
  echo "ERROR: Another test run is holding the lock ($LOCKFILE). Try again later." >&2
  exit 2
fi

exec bash scripts/test.sh "$@"

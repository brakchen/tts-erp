#!/usr/bin/env bash
# Run tts-erp tests by domain or layer.
#
# Usage:
#   scripts/test.sh                       # default: skip slow + requires_service
#   scripts/test.sh unit                  # layer_unit only, no slow
#   scripts/test.sh <domain>              # business domain (e.g. commerce, miaoshou, finance)
#   scripts/test.sh fast                  # not slow and not requires_service
#   scripts/test.sh all                   # everything, INCL. domain_migration (touches prod DB)
#   scripts/test.sh coverage              # with coverage report (incl. domain_migration)
#
# Domain names may be given with or without the "domain_" prefix
# (e.g. `scripts/test.sh miaoshou` == `scripts/test.sh domain_miaoshou`).
set -euo pipefail
cd "$(dirname "$0")/.."

PYTEST=".venv/bin/pytest"
if [ ! -x "$PYTEST" ]; then
  # Worktrees share the parent repo's venv; fall back to the absolute path.
  PARENT_VENV="$(cd .. && pwd)/$(basename "$(pwd)").../.venv/bin/pytest"
  PYTEST="/home/schan/tts-erp/.venv/bin/pytest"
fi

# addopts now carries `-m 'not domain_migration'` (2026-08-31): the migration
# suite re-applies one-shot scripts against the PROD DB and hangs full runs
# on lock waits. CLI -m overrides addopts, so the run_all/run_coverage
# tautology below is what makes `all` / `coverage` truly include them.
run_unit()       { "$PYTEST" -q -m "layer_unit and not slow" "$@"; }
run_fast()       { "$PYTEST" -q -m "not slow and not requires_service" "$@"; }
run_all()        { "$PYTEST" -q -m "domain_migration or not domain_migration" "$@"; }
run_coverage()   { "$PYTEST" -q -m "domain_migration or not domain_migration" --cov=tts_erp_v2 --cov=miaoshou --cov-report=term-missing "$@"; }
run_domain() {
  local name="${1#domain_}"
  "$PYTEST" -q -m "domain_${name} and not slow" "${@:2}"
}

case "${1:-default}" in
  unit)        shift; run_unit "$@" ;;
  fast)        shift; run_fast "$@" ;;
  all)         shift; run_all "$@" ;;
  coverage)    shift; run_coverage "$@" ;;
  default|"")  run_fast ;;
  -h|--help|help)
    sed -n '2,16p' "$0"
    ;;
  *)
    # First arg = domain name (with or without "domain_" prefix).
    run_domain "$@"
    ;;
esac
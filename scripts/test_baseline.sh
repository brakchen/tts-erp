#!/usr/bin/env bash
# Compare ONE fast-suite run against the committed master failure baseline.
#
# Why this exists
# ---------------
# The DoD used to read "compare against the master baseline, zero new stable
# failures", which in practice meant an agent had to run the whole suite at
# least twice (once to capture the master baseline, once after the change) plus
# one rerun per failure. At ~7 min per suite run that is 15-30 min of pure test
# execution and it is what pushed worker sessions past their wall-clock cap.
#
# The master baseline is a property of *master*, not of your lane, so it does
# not need to be re-measured per lane. It is committed once and only changes
# when master changes. A lane therefore needs exactly ONE suite run.
#
# Usage
# -----
#   bash scripts/test_baseline.sh                 # run fast once, compare, verdict
#   bash scripts/test_baseline.sh --scope unit    # any test_isolated.sh selection
#   bash scripts/test_baseline.sh --show          # print the committed baseline
#   bash scripts/test_baseline.sh --update        # regenerate the baseline (master only)
#   bash scripts/test_baseline.sh --keep-log PATH # also save the raw pytest output
#
# Exit codes
#   0  no new stable failures (this is the DoD pass condition)
#   1  new failures present, or the baseline file is missing/stale
#   2  usage error
#
# Safety: every run goes through scripts/test_isolated.sh, so the per-session
# ephemeral database clone and the production-shaped-name guard still apply.
set -euo pipefail

cd "$(dirname "$0")/.."

BASELINE_FILE="docs/testing/baseline-fast.txt"
SCOPE="fast"
MODE="compare"
KEEP_LOG=""
RUN_LOG=""
RUN_TMP=""

usage() {
  sed -n '2,30p' "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scope)
      SCOPE="${2:-}"
      shift 2
      ;;
    --update) MODE="update"; shift ;;
    --show) MODE="show"; shift ;;
    --keep-log)
      KEEP_LOG="${2:-}"
      shift 2
      ;;
    -h | --help | help)
      usage
      exit 0
      ;;
    *)
      echo "[test-baseline] unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ -n "$SCOPE" ]] || {
  echo "[test-baseline] --scope requires a value" >&2
  exit 2
}

fail() {
  echo "[test-baseline] ERROR: $*" >&2
  exit 2
}

RUN_TMP="$(mktemp -d)"
RUN_LOG="$RUN_TMP/pytest.log"
cleanup() {
  local rc=$?
  if [[ -n "$KEEP_LOG" ]]; then
    cp "$RUN_LOG" "$KEEP_LOG" 2>/dev/null || true
    echo "[test-baseline] raw pytest output: $KEEP_LOG"
  else
    rm -rf "$RUN_TMP"
  fi
  exit "$rc"
}
trap cleanup EXIT

# Normalize pytest's short summary into a sorted, reason-free id list.
#
# We deliberately strip the " - <reason>" tail: the reason text is unstable
# across runs (line numbers, assertion reprs, timings) and would make every
# diff look like churn. Only "which test id is red" is the DoD signal.
normalize_failures() {
  sed -n -E 's/^(FAILED|ERROR) ([^ ]+).*/\2/p' "$1" \
    | grep -v '^$' \
    | LC_ALL=C sort -u
}

case "$MODE" in
  show)
    [[ -f "$BASELINE_FILE" ]] || fail "baseline not found: $BASELINE_FILE"
    cat "$BASELINE_FILE"
    exit 0
    ;;
  update)
    ;;
  compare)
    [[ -f "$BASELINE_FILE" ]] || fail "baseline not found: $BASELINE_FILE — run 'bash scripts/test_baseline.sh --update' on master"
    ;;
esac

echo "[test-baseline] running scope '$SCOPE' once through the isolated runner"
echo "[test-baseline] this single run replaces the old capture-baseline + rerun-the-suite procedure"

# A failing suite is an expected outcome here (the baseline is non-empty), so
# do not let `set -e` abort before we get to read the summary.
rc=0
# shellcheck disable=SC2086
bash scripts/test_isolated.sh "$SCOPE" -p no:cacheprovider >"$RUN_LOG" 2>&1 || rc=$?

normalize_failures "$RUN_LOG" >"$RUN_TMP/current.txt"

if ! grep -qE "passed|no tests ran" "$RUN_LOG"; then
  fail "the suite did not produce a recognizable summary; inspect the run output"
fi

echo "[test-baseline] suite exit code: $rc"

if [[ "$MODE" == "update" ]]; then
  mkdir -p "$(dirname "$BASELINE_FILE")"
  {
    echo "# tts-erp committed test baseline"
    echo "#"
    echo "# Known-failing / erroring test ids for the selection below, measured on"
    echo "# master. This is a property of master, NOT of any feature lane."
    echo "#"
    echo "# Lane workflow (one suite run per lane):"
    echo "#   bash scripts/test_baseline.sh            # run + compare, exit 0 = pass"
    echo "#"
    echo "# Regenerate ONLY when master itself changes the failure set, and only"
    echo "# from a clean master worktree:"
    echo "#   bash scripts/test_baseline.sh --update"
    echo "#"
    echo "# scope:   $SCOPE"
    echo "# master:  $(git rev-parse HEAD)"
    echo "# updated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "# count:   $(wc -l <"$RUN_TMP/current.txt" | tr -d ' ')"
    echo "#"
    echo "# One normalized pytest test id per line. Sorted; no reason text."
    cat "$RUN_TMP/current.txt"
  } >"$BASELINE_FILE"
  echo "[test-baseline] wrote $BASELINE_FILE ($(wc -l <"$RUN_TMP/current.txt" | tr -d ' ') failing ids)"
  exit 0
fi

# compare mode
normalize_failures "$BASELINE_FILE" >"$RUN_TMP/base.txt"

LC_ALL=C comm -13 "$RUN_TMP/base.txt" "$RUN_TMP/current.txt" >"$RUN_TMP/new.txt"
LC_ALL=C comm -23 "$RUN_TMP/base.txt" "$RUN_TMP/current.txt" >"$RUN_TMP/fixed.txt"

new_count="$(wc -l <"$RUN_TMP/new.txt" | tr -d ' ')"
fixed_count="$(wc -l <"$RUN_TMP/fixed.txt" | tr -d ' ')"
base_count="$(wc -l <"$RUN_TMP/base.txt" | tr -d ' ')"

echo
echo "[test-baseline] baseline failing ids: $base_count"
echo "[test-baseline] this run failing ids:  $(wc -l <"$RUN_TMP/current.txt" | tr -d ' ')"
echo "[test-baseline] NEW stable failures:   $new_count"
echo "[test-baseline] newly passing:         $fixed_count"

if [[ "$fixed_count" -gt 0 ]]; then
  echo
  echo "Newly passing (master baseline is stale — regenerate on master):"
  sed 's/^/  + /' "$RUN_TMP/fixed.txt"
fi

if [[ "$new_count" -gt 0 ]]; then
  echo
  echo "NEW failures introduced by this lane:"
  sed 's/^/  - /' "$RUN_TMP/new.txt"
  echo
  echo "Classify each one before completing (docs/guides/agent-testing.md §6):"
  echo "  1. rerun the single smallest selection once through the isolated runner"
  echo "  2. if it passes in isolation it is an order/pollution flake, not a regression"
  echo "  3. if it fails consistently, it is real — fix it before completing"
  exit 1
fi

echo
echo "[test-baseline] PASS — zero new stable failures against the committed baseline"
exit 0

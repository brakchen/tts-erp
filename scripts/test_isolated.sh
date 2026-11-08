#!/usr/bin/env bash
# Sole test entrypoint: validate one connection authority, refresh/clone under a
# lock, execute against a unique test clone, then remove only that clone.
set -euo pipefail
cd "$(dirname "$0")/.."
# Dispatch only before any DB options/environment are consumed. Child runs own
# the unchanged connection authority, per-run proof and ephemeral cleanup.
if [[ "${1:-}" == --browser-workers ]]; then
  exec .venv/bin/python -m scripts._test_browser_parallel --workers "${2:-}" "${@:3}"
fi
KEEP_DB=0
REFRESH_TEMPLATE=0
TEMPLATE_DB="${TTS_ERP_TEST_TEMPLATE_DB:-tts_erp_test_template}"
RUN_DB="${TTS_ERP_TEST_DB_NAME:-}"
PG_DOCKER_VALUE="${PG_DOCKER-postgres}"
TEMPLATE_LOCK_PATH="${TTS_ERP_TEST_TEMPLATE_LOCK:-/tmp/tts-erp-test-template.lock}"
TEMPLATE_LOCK_TIMEOUT_S="${TTS_ERP_TEST_TEMPLATE_LOCK_TIMEOUT_S:-600}"
PYTHON="$(pwd)/.venv/bin/python"
RUN_STARTED_US="${EPOCHREALTIME/./}"
EXECUTION_STARTED_US=''
phase_timing() {
  local elapsed_us=$(( ${EPOCHREALTIME/./} - $2 ))
  printf '[test-timing] %s=%d.%03ds\n' "$1" "$((elapsed_us / 1000000))" "$((elapsed_us / 1000 % 1000))"
}

usage() {
  printf '%s\n' \
    'Usage: bash scripts/test_isolated.sh [options] [test.sh args...]' \
    '  --refresh-template   Rebuild the guarded test template before cloning' \
    '  --template-db NAME   Explicit test-named template (default tts_erp_test_template)' \
    '  --db-name NAME       Explicit test-named ephemeral database' \
    '  --keep-db            Preserve this run clone for diagnosis' \
    '  --browser-workers N browser FILE...  One/two isolated read-only workers (must be first option)' \
    '  -h, --help           Show help'
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --refresh-template) REFRESH_TEMPLATE=1; shift ;;
    --template-db) TEMPLATE_DB="${2:-}"; shift 2 ;;
    --db-name) RUN_DB="${2:-}"; shift 2 ;;
    --keep-db) KEEP_DB=1; shift ;;
    -h|--help|help) usage; exit 0 ;;
    --) shift; break ;;
    *) break ;;
  esac
done
fail() { echo "[isolated-test] ERROR: $*" >&2; exit 1; }
[[ -x "$PYTHON" ]] || fail 'missing project Python; run test dependency setup'

if [[ -z "${TTS_ERP_DB_URL_TEST:-}" && -f .env.test ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env.test
  set +a
fi
BASE_TEST_URL="${TTS_ERP_DB_URL_TEST:-${TTS_ERP_DB_URL:-}}"
[[ -n "$BASE_TEST_URL" ]] || fail 'TTS_ERP_DB_URL_TEST is unset; create .env.test'
if [[ -z "$RUN_DB" ]]; then
  branch="$(git branch --show-current 2>/dev/null || echo worktree)"
  seed="${branch}_${PI_SESSION_ID:-${USER:-agent}}_$(date -u +%Y%m%d%H%M%S)_$$"
  slug="$(printf '%s' "$seed" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '_')"
  short_hash="$(printf '%s' "$seed" | sha1sum | cut -c1-8)"
  RUN_DB="tts_erp_test_${slug:0:36}_${short_hash}"
fi
# Effective dbname overrides are consumed once, then REPLACED with the explicit
# lifecycle identities. Every command below uses this same server/user/port.
connections="$(TTS_ERP_DB_URL_TEST="$BASE_TEST_URL" PG_DOCKER="$PG_DOCKER_VALUE" \
  "$PYTHON" -m scripts._test_db_connections derive --template-db "$TEMPLATE_DB" --run-db "$RUN_DB")" || \
  fail 'test connection identity/routing validation failed'
mapfile -t connection_fields <<< "$connections"
[[ "${#connection_fields[@]}" -eq 6 ]] || fail 'invalid connection authority output'
phase_timing connection_prepare "$RUN_STARTED_US"
TEMPLATE_URL="${connection_fields[1]}"
RUN_URL="${connection_fields[2]}"
MAINTENANCE_URL="${connection_fields[3]}"
PG_DOCKER_PIN="${connection_fields[4]}"
# Bind the environment probe and every lifecycle exec to the same immutable ID.
if [[ -n "$PG_DOCKER_VALUE" ]]; then PG_DOCKER_VALUE="${connection_fields[5]}"; fi

pg_exec() {
  if [[ -n "$PG_DOCKER_VALUE" ]]; then
    docker --host "$PG_DOCKER_PIN" exec "$PG_DOCKER_VALUE" "$@"
  else command "$@"; fi
}
plain_pg_url() { printf '%s' "${1/postgresql+psycopg:\/\//postgresql://}"; }
db_exists() {
  local found
  found="$(pg_exec psql --no-psqlrc --tuples-only --no-align --quiet \
    --command "SELECT 1 FROM pg_database WHERE datname = '$TEMPLATE_DB'" "$MAINTENANCE_URL")" || \
    fail 'cannot inspect template existence on the selected server'
  [[ "$found" == 1 ]]
}
drop_run_db() {
  pg_exec dropdb --maintenance-db "$MAINTENANCE_URL" --if-exists --force "$RUN_DB" >/dev/null 2>&1 || \
    pg_exec dropdb --maintenance-db "$MAINTENANCE_URL" --if-exists "$RUN_DB" >/dev/null 2>&1
}
read_env_key() {
  FILE_PATH="$1" KEY_NAME="$2" "$PYTHON" - <<'PY'
import os
from pathlib import Path
p = Path(os.environ["FILE_PATH"])
if p.exists():
    for raw in p.read_text().splitlines():
        key, sep, value = raw.strip().partition("=")
        if sep and key.strip() == os.environ["KEY_NAME"]:
            print(value.strip().strip('"').strip("'"))
            break
PY
}
source_prod_url() {
  if [[ -n "${TTS_ERP_DB_URL_PROD_SOURCE:-}" ]]; then printf '%s' "$TTS_ERP_DB_URL_PROD_SOURCE"; return; fi
  local url
  url="$(read_env_key .env TTS_ERP_DB_URL)"
  [[ -n "$url" ]] || url="$(read_env_key ../../.env TTS_ERP_DB_URL)"
  printf '%s' "$url"
}
prod_alembic_revision() {
  pg_exec psql --no-psqlrc --tuples-only --no-align --quiet \
    --command 'SELECT version_num FROM alembic_version LIMIT 1' "$(plain_pg_url "$1")" 2>/dev/null | head -1 || true
}
code_tree_alembic_head() {
  local alembic
  alembic="${TTS_ERP_TEST_ALEMBIC_BIN:-.venv/bin/alembic}"
  [[ -x "$alembic" ]] || alembic='/home/schan/tts-erp/.venv/bin/alembic'
  [[ -x "$alembic" ]] || return 0
  "$alembic" heads 2>/dev/null | awk -F'[ (]' '/\(head\)/{print $1; exit}'
}
# Diagnose a clone whose schema revision no longer matches this checkout.
# The clone path never upgrades the clone, so a clone revision below the code
# head means the checkout is stale relative to the template — an environment
# condition, not a code defect. Report it here instead of leaving the caller
# to infer it from a downstream assertion failure.
warn_revision_drift() {
  local clone_revision code_head
  clone_revision="$(pg_exec psql --no-psqlrc --tuples-only --no-align --quiet \
    --command 'SELECT version_num FROM alembic_version LIMIT 1' "$(plain_pg_url "$RUN_URL")" 2>/dev/null | head -1 || true)"
  [[ -n "$clone_revision" ]] || return 0
  code_head="$(code_tree_alembic_head)"
  [[ -n "$code_head" ]] || return 0
  [[ "$clone_revision" == "$code_head" ]] && return 0
  {
    echo "[isolated-test] WARNING: clone schema revision does not match this checkout" >&2
    echo "  clone revision : $clone_revision  (copied from $TEMPLATE_DB)" >&2
    echo "  code head      : $code_head  (this checkout)" >&2
    echo "  The clone path never upgrades the clone, so a stale checkout always" >&2
    echo "  produces this drift. It is an environment condition, not a code defect." >&2
    echo "  Fix: sync this checkout to origin/master, then refresh the template" >&2
    echo "        git pull --ff-only origin master" >&2
    echo "        bash scripts/test_isolated.sh --refresh-template fast" >&2
    echo "        (--refresh-template needs a read-only production-shape source URL;" >&2
    echo "         see docs/ops/tts-erp.md)" >&2
  }
}
TEMPLATE_CREATED_OR_REFRESHED=0
refresh_template() {
  echo "[isolated-test] refreshing template DB: $TEMPLATE_DB"
  local prod_url revision alembic python_bin
  prod_url="$(source_prod_url)"
  [[ -n "$prod_url" ]] || fail 'cannot resolve production schema source URL'
  # The importer owns preflight, complete dump and guarded target replacement.
  # Do not mutate target-only tables or create an empty template before it.
  TTS_ERP_DB_URL="$prod_url" TTS_ERP_DB_URL_TEST="$TEMPLATE_URL" PG_DOCKER="$PG_DOCKER_VALUE" \
    bash scripts/import_prod_to_test.sh --schema-only --recreate-test-db --yes
  revision="$(prod_alembic_revision "$prod_url")"
  alembic="${TTS_ERP_TEST_ALEMBIC_BIN:-.venv/bin/alembic}"
  [[ -x "$alembic" ]] || alembic='/home/schan/tts-erp/.venv/bin/alembic'
  if [[ -x "$alembic" && -n "$revision" ]]; then
    if compgen -G "alembic/versions/${revision}*.py" >/dev/null; then
      echo "[isolated-test] stamping template at prod revision: $revision"
      TTS_ERP_DB_URL="$TEMPLATE_URL" "$alembic" stamp "$revision"
      echo '[isolated-test] upgrading template to worktree head'
      TTS_ERP_DB_URL="$TEMPLATE_URL" "$alembic" upgrade head
      python_bin="${alembic%/alembic}/python"
      if [[ -x "$python_bin" ]]; then
        echo '[isolated-test] seeding built-in test roles and permissions'
        TTS_ERP_DB_URL="$TEMPLATE_URL" "$python_bin" -m tts_erp_v2.accounts.cli sync-permissions
      fi
    else
      echo '[isolated-test] WARNING: source revision is absent; imported schema was not upgraded' >&2
    fi
  else echo '[isolated-test] WARNING: source revision/alembic unavailable; imported schema was not upgraded' >&2; fi
  TEMPLATE_CREATED_OR_REFRESHED=1
}
RUN_CREATED=0
RUN_PROOF_DIR=''
RUN_PROOF_PATH=''
RUN_ID=''
prepare_template_and_clone() {
  local phase_started_us="${EPOCHREALTIME/./}"
  exec 9>"$TEMPLATE_LOCK_PATH"
  flock -w "$TEMPLATE_LOCK_TIMEOUT_S" 9 || fail 'could not acquire template lock'
  phase_timing template_lock "$phase_started_us"
  phase_started_us="${EPOCHREALTIME/./}"
  if [[ "$REFRESH_TEMPLATE" -eq 1 ]]; then refresh_template
  elif ! db_exists; then refresh_template; fi
  phase_timing template_prepare "$phase_started_us"
  phase_started_us="${EPOCHREALTIME/./}"
  echo "[isolated-test] cloning $TEMPLATE_DB -> $RUN_DB"
  drop_run_db
  pg_exec createdb --maintenance-db "$MAINTENANCE_URL" -T "$TEMPLATE_DB" "$RUN_DB"
  RUN_CREATED=1
  phase_timing database_clone "$phase_started_us"
  warn_revision_drift
  flock -u 9
  exec 9>&-
}
issue_run_binding() {
  [[ "$RUN_CREATED" -eq 1 ]] || fail 'cannot issue proof before clone creation'
  RUN_PROOF_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tts-erp-test-run.XXXXXXXX")"
  RUN_PROOF_PATH="$RUN_PROOF_DIR/run.json"
  RUN_ID="$("$PYTHON" -c 'import secrets; print(secrets.token_hex(32))')"
  TTS_ERP_DB_URL_TEST="$RUN_URL" "$PYTHON" -m scripts._test_db_connections issue-run-proof \
    --base-db "${connection_fields[0]}" --template-db "$TEMPLATE_DB" --run-db "$RUN_DB" \
    --run-id "$RUN_ID" --owner-pid "$$" --proof-file "$RUN_PROOF_PATH"
}
cleanup_status=1
if [[ "${1:-}" == selected ]]; then cleanup_status=70; fi
cleanup() {
  local rc=$?
  local cleanup_started_us="${EPOCHREALTIME/./}"
  if [[ -n "$EXECUTION_STARTED_US" ]]; then phase_timing test_execution "$EXECUTION_STARTED_US"; fi
  # keep-db preserves data for diagnosis, never current-run write provenance.
  if [[ -n "$RUN_PROOF_DIR" ]]; then
    if ! rm -f -- "$RUN_PROOF_PATH" || ! rmdir -- "$RUN_PROOF_DIR"; then
      echo '[isolated-test] ERROR: run proof cleanup failed' >&2
      if [[ "$rc" -eq 0 || "$cleanup_status" -ne 1 ]]; then rc="$cleanup_status"; fi
    fi
  fi
  if [[ "$RUN_CREATED" -eq 1 ]]; then
    if [[ "$KEEP_DB" -eq 1 ]]; then echo "[isolated-test] keeping ephemeral DB: $RUN_DB"
    else
      echo "[isolated-test] dropping ephemeral DB: $RUN_DB"
      if ! drop_run_db; then
        echo "[isolated-test] ERROR: ephemeral cleanup failed: $RUN_DB" >&2
        if [[ "$rc" -eq 0 || "$cleanup_status" -ne 1 ]]; then rc="$cleanup_status"; fi
      fi
    fi
  fi
  phase_timing database_cleanup "$cleanup_started_us"
  phase_timing isolated_total "$RUN_STARTED_US"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
prepare_template_and_clone
issue_run_binding
if [[ "$TEMPLATE_CREATED_OR_REFRESHED" -eq 0 ]]; then echo "[isolated-test] using existing template DB: $TEMPLATE_DB"; fi
echo "[isolated-test] running: bash scripts/test.sh ${*:-}"
EXECUTION_STARTED_US="${EPOCHREALTIME/./}"
TTS_ERP_DB_URL_TEST="$RUN_URL" TTS_ERP_TEST_RUN_PROOF="$RUN_PROOF_PATH" \
  TTS_ERP_TEST_RUN_ID="$RUN_ID" TTS_ERP_TEST_REFRESHED_FOR_SELECTION="$REFRESH_TEMPLATE" \
  bash scripts/test.sh "$@"

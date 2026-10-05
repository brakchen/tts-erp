#!/usr/bin/env bash
# Run tests against a per-session database cloned from a maintained template DB.
#
# Default flow:
#   1. Ensure template DB exists (default: tts_erp_test_template).
#   2. Clone it to a unique ephemeral DB.
#   3. Run scripts/test.sh with TTS_ERP_DB_URL_TEST pointing at that DB.
#   4. Drop the ephemeral DB unless --keep-db was supplied.
#
# Template refresh:
#   bash scripts/test_isolated.sh --refresh-template fast
#
# The template is schema-only by default. It is bootstrapped from the production
# schema through scripts/import_prod_to_test.sh --schema-only. When this worktree
# contains the production alembic revision, the template is stamped there and
# upgraded to this worktree's alembic head.
set -euo pipefail

cd "$(dirname "$0")/.."

KEEP_DB=0
REFRESH_TEMPLATE=0
TEMPLATE_DB="${TTS_ERP_TEST_TEMPLATE_DB:-tts_erp_test_template}"
RUN_DB="${TTS_ERP_TEST_DB_NAME:-}"
PG_DOCKER_DEFAULT="postgres"
PG_DOCKER_VALUE="${PG_DOCKER-$PG_DOCKER_DEFAULT}"
TEMPLATE_LOCK_PATH="${TTS_ERP_TEST_TEMPLATE_LOCK:-/tmp/tts-erp-test-template.lock}"
TEMPLATE_LOCK_TIMEOUT_S="${TTS_ERP_TEST_TEMPLATE_LOCK_TIMEOUT_S:-600}"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/test_isolated.sh [options] [test.sh args...]

Examples:
  bash scripts/test_isolated.sh fast
  bash scripts/test_isolated.sh --refresh-template fast
  bash scripts/test_isolated.sh api tests/api/test_auth_login.py::test_login_sets_cookie
  bash scripts/test_isolated.sh --keep-db fast

Options:
  --refresh-template   Rebuild tts_erp_test_template before cloning.
  --template-db NAME   Use a different template DB name. Must contain "test".
  --db-name NAME       Use a specific ephemeral DB name. Must contain "test".
  --keep-db            Do not drop the ephemeral DB after the run.
  -h, --help           Show this help.

Environment:
  TTS_ERP_DB_URL_TEST          Base test DB URL loaded from .env.test if unset.
  TTS_ERP_TEST_TEMPLATE_DB     Default template DB name override.
  TTS_ERP_TEST_DB_NAME         Default ephemeral DB name override.
  TTS_ERP_DB_URL_PROD_SOURCE   Optional schema source URL for template refresh;
                              defaults to .env, then ../../.env.
  TTS_ERP_TEST_TEMPLATE_LOCK  Template refresh/clone lock path; default
                              /tmp/tts-erp-test-template.lock.
  TTS_ERP_TEST_TEMPLATE_LOCK_TIMEOUT_S
                              Seconds to wait for the template lock; default 600.
  PG_DOCKER                   Docker container for postgres commands; set to
                              empty to use host psql/createdb/dropdb.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --refresh-template)
      REFRESH_TEMPLATE=1
      shift
      ;;
    --template-db)
      TEMPLATE_DB="${2:-}"
      shift 2
      ;;
    --db-name)
      RUN_DB="${2:-}"
      shift 2
      ;;
    --keep-db)
      KEEP_DB=1
      shift
      ;;
    -h|--help|help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    *)
      break
      ;;
  esac
done

fail() {
  echo "[isolated-test] ERROR: $*" >&2
  exit 1
}

pg_exec() {
  if [[ -n "$PG_DOCKER_VALUE" ]]; then
    docker exec "$PG_DOCKER_VALUE" "$@"
  else
    command "$@"
  fi
}

validate_db_name() {
  local name="$1" label="$2"
  [[ -n "$name" ]] || fail "$label database name is empty"
  [[ "$name" =~ ^[A-Za-z0-9_]+$ ]] || fail "$label database name must match ^[A-Za-z0-9_]+$: $name"
  [[ "${#name}" -le 63 ]] || fail "$label database name is longer than 63 bytes: $name"
  case "$name" in
    tts_erp|tts_erp_prod|tts_erp_prod_*)
      fail "$label database name is production-shaped: $name"
      ;;
  esac
  [[ "$name" == *test* || "$name" == *_v3* ]] || fail "$label database name must contain 'test' or '_v3': $name"
}

replace_db_name() {
  local url="$1" db="$2"
  DB_NAME="$db" python3 - "$url" <<'PY'
from __future__ import annotations

import os
import sys
from urllib.parse import urlparse, urlunparse

url = sys.argv[1]
db = os.environ["DB_NAME"]
parsed = urlparse(url)
if not parsed.scheme or not parsed.netloc:
    raise SystemExit(f"invalid PostgreSQL URL: {url!r}")
print(urlunparse(parsed._replace(path="/" + db)))
PY
}

plain_pg_url() {
  python3 - "$1" <<'PY'
from __future__ import annotations

import sys
url = sys.argv[1]
print(url.replace("postgresql+psycopg://", "postgresql://", 1))
PY
}

url_db_name() {
  python3 - "$1" <<'PY'
from __future__ import annotations

import sys
from urllib.parse import urlparse
print((urlparse(sys.argv[1]).path or "").lstrip("/"))
PY
}

read_env_key() {
  local file="$1" key="$2"
  FILE_PATH="$file" KEY_NAME="$key" python3 - <<'PY'
from __future__ import annotations

import os
from pathlib import Path

path = Path(os.environ["FILE_PATH"])
key = os.environ["KEY_NAME"]
if not path.exists():
    raise SystemExit(0)
for raw in path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    if k.strip() == key:
        print(v.strip().strip('"').strip("'"))
        break
PY
}

if [[ -z "${TTS_ERP_DB_URL_TEST:-}" && -f .env.test ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env.test
  set +a
fi

BASE_TEST_URL="${TTS_ERP_DB_URL_TEST:-}"
if [[ -z "$BASE_TEST_URL" && -n "${TTS_ERP_DB_URL:-}" ]]; then
  # Some local .env.test files predate TTS_ERP_DB_URL_TEST and set
  # TTS_ERP_DB_URL directly. Accept it only if it is clearly test-shaped.
  candidate_db="$(url_db_name "$TTS_ERP_DB_URL")"
  if [[ "$candidate_db" == *test* || "$candidate_db" == *_v3* ]]; then
    BASE_TEST_URL="$TTS_ERP_DB_URL"
  fi
fi
[[ -n "$BASE_TEST_URL" ]] || fail "TTS_ERP_DB_URL_TEST is unset; create .env.test or export it"

BASE_TEST_DB="$(url_db_name "$BASE_TEST_URL")"
validate_db_name "$BASE_TEST_DB" "base test"
validate_db_name "$TEMPLATE_DB" "template"

if [[ -z "$RUN_DB" ]]; then
  branch="$(git branch --show-current 2>/dev/null || echo worktree)"
  session="${PI_SESSION_ID:-${USER:-agent}}"
  seed="${branch}_${session}_$(date -u +%Y%m%d%H%M%S)_$$"
  slug="$(printf '%s' "$seed" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/_/g; s/^_+//; s/_+$//')"
  short_hash="$(printf '%s' "$seed" | sha1sum | awk '{print substr($1,1,8)}')"
  RUN_DB="tts_erp_test_${slug:0:36}_${short_hash}"
fi
validate_db_name "$RUN_DB" "ephemeral"
[[ "$RUN_DB" != "$TEMPLATE_DB" ]] || fail "ephemeral DB must differ from template DB"
[[ "$RUN_DB" != "$BASE_TEST_DB" ]] || fail "ephemeral DB must differ from base .env.test DB"

TEMPLATE_URL="$(replace_db_name "$BASE_TEST_URL" "$TEMPLATE_DB")"
RUN_URL="$(replace_db_name "$BASE_TEST_URL" "$RUN_DB")"

TEMPLATE_CREATED_OR_REFRESHED=0

db_exists() {
  local db="$1"
  local found
  found="$(pg_exec psql -U postgres -d postgres --no-psqlrc --tuples-only --no-align --quiet \
    --command "SELECT 1 FROM pg_database WHERE datname = '$db'" 2>/dev/null || true)"
  [[ "$found" == "1" ]]
}

create_empty_db() {
  local db="$1"
  pg_exec createdb -U postgres "$db"
}

drop_db() {
  local db="$1"
  pg_exec dropdb -U postgres --if-exists --force "$db" >/dev/null 2>&1 || \
    pg_exec dropdb -U postgres --if-exists "$db" >/dev/null 2>&1 || true
}

source_prod_url() {
  if [[ -n "${TTS_ERP_DB_URL_PROD_SOURCE:-}" ]]; then
    printf '%s\n' "$TTS_ERP_DB_URL_PROD_SOURCE"
    return
  fi
  # Prefer .env directly so a legacy .env.test that set TTS_ERP_DB_URL to a
  # test DB cannot accidentally become the import source. Worktrees normally
  # do not have their own .env, so fall back to the main checkout's ../../.env.
  local url
  url="$(read_env_key .env TTS_ERP_DB_URL)"
  if [[ -z "$url" ]]; then
    url="$(read_env_key ../../.env TTS_ERP_DB_URL)"
  fi
  printf '%s\n' "$url"
}

prod_alembic_revision() {
  local prod_url="$1"
  [[ -n "$prod_url" ]] || return 0
  local prod_plain
  prod_plain="$(plain_pg_url "$prod_url")"
  pg_exec psql --no-psqlrc --tuples-only --no-align --quiet \
    --command "SELECT version_num FROM alembic_version LIMIT 1" \
    "$prod_plain" 2>/dev/null | head -1 || true
}

refresh_template() {
  echo "[isolated-test] refreshing template DB: $TEMPLATE_DB"
  drop_db "$TEMPLATE_DB"
  create_empty_db "$TEMPLATE_DB"

  local prod_url revision alembic
  prod_url="$(source_prod_url)"
  [[ -n "$prod_url" ]] || fail "cannot resolve production schema source URL from TTS_ERP_DB_URL_PROD_SOURCE, .env, or ../../.env"

  # Run the existing guarded importer with an explicit source URL so worktrees
  # do not need a writable .env symlink.
  TTS_ERP_DB_URL="$prod_url" \
    TTS_ERP_DB_URL_TEST="$TEMPLATE_URL" \
    PG_DOCKER="$PG_DOCKER_VALUE" \
    bash scripts/import_prod_to_test.sh --schema-only --yes

  revision="$(prod_alembic_revision "$prod_url")"
  alembic=".venv/bin/alembic"
  if [[ ! -x "$alembic" ]]; then
    alembic="/home/schan/tts-erp/.venv/bin/alembic"
  fi
  if [[ -x "$alembic" && -n "$revision" ]]; then
    if compgen -G "alembic/versions/${revision}*.py" >/dev/null; then
      echo "[isolated-test] stamping template at prod revision: $revision"
      TTS_ERP_DB_URL="$TEMPLATE_URL" "$alembic" stamp "$revision"
      echo "[isolated-test] upgrading template to worktree head"
      TTS_ERP_DB_URL="$TEMPLATE_URL" "$alembic" upgrade head
      local python_bin="${alembic%/alembic}/python"
      if [[ -x "$python_bin" ]]; then
        echo "[isolated-test] seeding built-in test roles and permissions"
        TTS_ERP_DB_URL="$TEMPLATE_URL" \
          "$python_bin" -m tts_erp_v2.accounts.cli sync-permissions
      fi
    else
      echo "[isolated-test] WARNING: prod alembic revision $revision is not present in this worktree;" >&2
      echo "                 leaving template at imported schema without alembic upgrade" >&2
    fi
  elif [[ ! -x "$alembic" ]]; then
    echo "[isolated-test] WARNING: alembic not found; template not upgraded beyond imported schema" >&2
  else
    echo "[isolated-test] WARNING: prod alembic revision not found; template not upgraded beyond imported schema" >&2
  fi
  TEMPLATE_CREATED_OR_REFRESHED=1
}

prepare_template_and_clone() {
  exec 9>"$TEMPLATE_LOCK_PATH"
  if ! flock -w "$TEMPLATE_LOCK_TIMEOUT_S" 9; then
    fail "could not acquire template lock within ${TEMPLATE_LOCK_TIMEOUT_S}s: $TEMPLATE_LOCK_PATH"
  fi

  if [[ "$REFRESH_TEMPLATE" -eq 1 ]]; then
    refresh_template
  elif ! db_exists "$TEMPLATE_DB"; then
    echo "[isolated-test] template DB missing; bootstrapping: $TEMPLATE_DB"
    refresh_template
  fi

  echo "[isolated-test] cloning $TEMPLATE_DB -> $RUN_DB"
  drop_db "$RUN_DB"
  pg_exec createdb -U postgres -T "$TEMPLATE_DB" "$RUN_DB"

  # Release the template lock before running tests. The clone is now independent,
  # so other sessions may clone/refresh the template while this suite runs.
  flock -u 9
  exec 9>&-
}

cleanup() {
  local rc=$?
  if [[ "$KEEP_DB" -eq 1 ]]; then
    echo "[isolated-test] keeping ephemeral DB: $RUN_DB"
  else
    echo "[isolated-test] dropping ephemeral DB: $RUN_DB"
    drop_db "$RUN_DB"
  fi
  exit "$rc"
}
trap cleanup EXIT INT TERM

prepare_template_and_clone

if [[ "$TEMPLATE_CREATED_OR_REFRESHED" -eq 0 ]]; then
  echo "[isolated-test] using existing template DB: $TEMPLATE_DB"
fi

echo "[isolated-test] running: bash scripts/test.sh ${*:-}"
TTS_ERP_DB_URL_TEST="$RUN_URL" bash scripts/test.sh "$@"

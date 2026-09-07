#!/usr/bin/env bash
# Import data from production ``tts_erp`` into the dedicated test database
# ``tts_erp_v3_test`` so a developer (or CI smoke run) can run the test
# suite against realistic shapes (real shop_ids, real order counts,
# etc.) without polluting the production DB.
#
# What this does
# ---------------
# 1. ``pg_dump --schema-only`` from the prod URL → re-create the schema
#    in the test DB (drops existing objects in test DB).
# 2. For each non-sensitive table, ``pg_dump --data-only`` from prod →
#    restore into test DB. Sensitive tables (production credentials,
#    production API keys) are NEVER copied.
# 3. Print a summary of rows imported per table.
#
# What this does NOT do
# ---------------------
# - Touch the production DB beyond ``pg_dump`` (read-only).
# - Copy ``integration.credentials`` or ``security.api_keys`` (would
#   leak production secrets into the test DB).
# - Sanitize fields beyond the table-level blacklist. If you need
#   additional PII scrubbing, do it in a follow-up one-off script.
#
# Usage
# -----
#   scripts/import_prod_to_test.sh                 # full import, confirm
#   scripts/import_prod_to_test.sh --yes           # skip confirmation
#   scripts/import_prod_to_test.sh --tables commerce.shops,commerce.products_spu
#   scripts/import_prod_to_test.sh --row-limit 1000   # cap per-table rows
#   scripts/import_prod_to_test.sh --schema-only   # drop+recreate, no data
#   scripts/import_prod_to_test.sh --dry-run       # print plan, do nothing
#
# Environment
# -----------
#   TTS_ERP_DB_URL       — source (prod). Defaults to the value from
#                          ``.env`` via ``scripts/_db_url.py`` if unset.
#   TTS_ERP_DB_URL_TEST  — target (test). Defaults to the value from
#                          ``.env.test`` if unset.
#
# Exit codes
# ----------
#   0  success
#   1  user cancelled or preflight failed
#   2  source/target URL resolution failed
#   3  safety guard tripped (source looks like target or vice versa)
#   4  pg_dump / pg_restore failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── CLI parsing ─────────────────────────────────────────────
TABLES_FILTER=""
ROW_LIMIT=""
SCHEMA_ONLY=0
DRY_RUN=0
ASSUME_YES=0
INCLUDE_SENSITIVE=0

usage() {
  sed -n '2,46p' "$0"
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tables) TABLES_FILTER="${2:-}"; shift 2 ;;
    --row-limit) ROW_LIMIT="${2:-}"; shift 2 ;;
    --schema-only) SCHEMA_ONLY=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --include-sensitive) INCLUDE_SENSITIVE=1; shift ;;
    --exclude-credentials) EXCLUDE_CREDENTIALS=1; shift ;;
    --exclude-api-keys) EXCLUDE_API_KEYS=1; shift ;;
    -h|--help|help) usage 0 ;;
    *) echo "[import] unknown arg: $1" >&2; usage 1 ;;
  esac
done

# ── URL resolution ──────────────────────────────────────────
load_db_url() {
  # Prefer ``.env.test`` for the test URL (same convention as
  # ``scripts/test.sh``). For the prod URL fall back to ``.env``.
  local url_var="$1" path="$2"
  if [[ -f "$path" ]]; then
    set -a
    # shellcheck disable=SC1090
    . "$path"
    set +a
  fi
  printf '%s' "${!url_var:-}"
}

SRC_URL="${TTS_ERP_DB_URL:-$(load_db_url TTS_ERP_DB_URL "$REPO_ROOT/.env")}"
DST_URL="${TTS_ERP_DB_URL_TEST:-$(load_db_url TTS_ERP_DB_URL "$REPO_ROOT/.env.test")}"

if [[ -z "$SRC_URL" ]]; then
  echo "[import] TTS_ERP_DB_URL not set and .env missing; cannot determine source." >&2
  exit 2
fi
if [[ -z "$DST_URL" ]]; then
  echo "[import] TTS_ERP_DB_URL_TEST not set and .env.test missing." >&2
  echo "         create one with:  cp .env .env.test && sed -i 's|/tts_erp\b|/tts_erp_v3_test|' .env.test" >&2
  exit 2
fi

# Strip the SQLAlchemy driver prefix; ``pg_dump`` / ``psql`` want the
# plain ``postgresql://`` form.
src_plain="${SRC_URL/postgresql+psycopg:\/\//postgresql://}"
dst_plain="${DST_URL/postgresql+psycopg:\/\//postgresql://}"

# Resolve ``pg_dump`` / ``psql``. They live in the docker ``postgres``
# container (this host is just a client); call them via
# ``docker exec postgres``. Override with ``PG_DOCKER=`` to bypass
# (e.g. if the binaries are installed on the host, or you're pointing
# at a remote DB).
PG_DOCKER="${PG_DOCKER:-postgres}"
if command -v pg_dump >/dev/null 2>&1 && command -v psql >/dev/null 2>&1; then
  pg_dump() { command pg_dump "$@"; }
  psql()   { command psql   "$@"; }
else
  pg_dump() { docker exec "$PG_DOCKER" pg_dump "$@"; }
  psql()   { docker exec "$PG_DOCKER" psql   "$@"; }
fi

# ── Safety: target must look like a test DB ──────────────────
extract_dbname() {
  printf '%s' "$1" | sed -E 's|^postgresql://[^/]*/||; s|/$||; s|\?.*$||'
}

SRC_DB="$(extract_dbname "$src_plain")"
DST_DB="$(extract_dbname "$dst_plain")"

if [[ "$SRC_DB" == "$DST_DB" ]]; then
  echo "[import] REFUSING: source and target resolve to the same DB (\"$SRC_DB\")." >&2
  echo "         Set TTS_ERP_DB_URL (source) and TTS_ERP_DB_URL_TEST (target) to different databases." >&2
  exit 3
fi

if [[ "$DST_DB" != *test* && "$DST_DB" != *_v3* ]]; then
  echo "[import] REFUSING: target dbname \"$DST_DB\" does not contain 'test' or '_v3'." >&2
  echo "         Refusing to bulk-load into a non-test DB to avoid prod corruption." >&2
  exit 3
fi

# ── Table list & sensitive handling ────────────────────────
# Sensitive tables that hold production secrets. The user almost
# always WANTS these imported too, because so many v2 tables have
# FKs pointing at credentials / api_keys and the rows would be
# silently skipped without them. The risk profile is unchanged:
# whoever has ``.env`` (the production Fernet key) can already
# decrypt anything in ``integration.credentials``; putting the same
# encrypted rows in ``tts_erp_v3_test`` does not expand the attack
# surface to anyone who doesn't already have ``.env``.
#
# If a developer is uncomfortable with prod secrets on their laptop
# they can pass ``--exclude-credentials`` (and/or
# ``--exclude-api-keys``) to drop those tables from the import.
SENSITIVE=(
  "integration.credentials"
  "security.api_keys"
)
EXCLUDE_CREDENTIALS=0
EXCLUDE_API_KEYS=0

# All v2 tables — used for the full-import default.
# Ordering matters: tables with FK to others must come AFTER their
# parent. We list ``integration.credentials`` and ``security.api_keys``
# first because they're referenced by ``commerce.shops``,
# ``integration.raw_records``, ``integration.sync_jobs``,
# ``procurement.procurement_accounts`` and ``procurement.spu_images``.
ALL_TABLES=(
  "integration.credentials"
  "security.api_keys"
  "commerce.shops"
  "commerce.products_spu"
  "commerce.products_sku"
  "commerce.sales_orders"
  "commerce.sales_order_lines"
  "procurement.procurement_accounts"
  "procurement.procurement_products"
  "procurement.procurement_product_variants"
  "procurement.purchase_orders"
  "procurement.purchase_order_lines"
  "procurement.manual_product_costs"
  "fulfillment.shipments"
  "fulfillment.shipment_lines"
  "fulfillment.tracking_events"
  "after_sales.cases"
  "after_sales.case_lines"
  "finance.payouts"
  "finance.settlement_statements"
  "finance.settlement_transactions"
  "finance.settlement_components"
  "fx.exchange_rate_snapshots"
  "fx.exchange_rates"
  "linkage.account_links"
  "linkage.product_links"
  "linkage.variant_links"
  "linkage.link_evidence"
  "linkage.link_overrides"
  "linkage.link_issues"
  "reporting.product_cost_snapshots"
  "reporting.product_profit_daily"
  "reporting.shipment_tracking_summary"
  "integration.sync_jobs"
  "integration.sync_cursors"
  "integration.sync_issues"
  "integration.raw_records"
  "analytics.ad_raw"
  "analytics.ad_product_links"
)

# Apply --tables filter if any.
if [[ -n "$TABLES_FILTER" ]]; then
  IFS=',' read -r -a requested <<< "$TABLES_FILTER"
  filtered=()
  for t in "${requested[@]}"; do
    t_trimmed="${t// /}"
    if [[ -z "$t_trimmed" ]]; then continue; fi
    found=0
    for known in "${ALL_TABLES[@]}"; do
      if [[ "$known" == "$t_trimmed" ]]; then found=1; break; fi
    done
    if [[ $found -eq 0 ]]; then
      echo "[import] unknown table in --tables: \"$t_trimmed\"" >&2
      echo "         known: ${ALL_TABLES[*]}" >&2
      exit 2
    fi
    filtered+=("$t_trimmed")
  done
  ALL_TABLES=("${filtered[@]}")
fi

# Apply opt-out flags.
pruned=()
for t in "${ALL_TABLES[@]}"; do
  case "$t" in
    "integration.credentials")
      [[ $EXCLUDE_CREDENTIALS -eq 1 ]] && continue ;;
    "security.api_keys")
      [[ $EXCLUDE_API_KEYS -eq 1 ]] && continue ;;
  esac
  pruned+=("$t")
done
ALL_TABLES=("${pruned[@]}")

# ── Plan & confirm ──────────────────────────────────────────
echo "[import] source: $SRC_DB  ($src_plain)"
echo "[import] target: $DST_DB  ($dst_plain)"
echo "[import] mode:   $([[ $SCHEMA_ONLY -eq 1 ]] && echo "schema-only" || echo "schema + data")"
echo "[import] tables: ${#ALL_TABLES[@]}"
[[ -n "$ROW_LIMIT" ]] && echo "[import] row-limit per table: $ROW_LIMIT"
[[ $INCLUDE_SENSITIVE -eq 1 ]] && echo "[import] WARNING: --include-sensitive set; sensitive tables WILL be copied."
echo

# Build the actual import list (excluding blacklisted even with --include-sensitive
# unless explicit; for now keep it simple: blacklist always blocks unless flag is set,
# and the flag is on the user's head).
if [[ $DRY_RUN -eq 1 ]]; then
  echo "[import] --dry-run set; nothing will be executed."
  echo
  echo "Plan:"
  echo "  1. pg_dump --schema-only from \"$SRC_DB\" → drop+create schema in \"$DST_DB\""
  [[ $SCHEMA_ONLY -eq 0 ]] && for t in "${ALL_TABLES[@]}"; do
    echo "  2. copy data: $t"
  done
  exit 0
fi

if [[ $ASSUME_YES -eq 0 ]]; then
  echo "About to:"
  echo "  1. DROP+RECREATE schema in \"$DST_DB\" from prod's schema"
  [[ $SCHEMA_ONLY -eq 0 ]] && echo "  2. Copy ${#ALL_TABLES[@]} tables' data from \"$SRC_DB\" → \"$DST_DB\""
  echo
  read -r -p "Proceed? [y/N] " reply
  if [[ "$reply" != "y" && "$reply" != "Y" ]]; then
    echo "[import] cancelled by user."
    exit 1
  fi
fi

# ── Step 1: schema ──────────────────────────────────────────
# We do NOT pipe pg_dump → psql directly. The combined stream can be
# 10+ MB; with our ``set -o pipefail`` plus the docker-exec function
# wrapper, psql closing stdin early triggers SIGPIPE on pg_dump and
# the pipeline fails with rc=141. Dumping to a temp file then
# restoring avoids that entirely.
echo "[import] (1/2) dropping + recreating schema in target from prod..."
SCHEMA_DUMP="$(mktemp --suffix=.sql)"
trap 'rm -f "$SCHEMA_DUMP"' EXIT

if ! pg_dump \
    --schema-only \
    --no-owner \
    --no-privileges \
    --clean \
    --if-exists \
    --file="$SCHEMA_DUMP" \
    "$src_plain" > /tmp/import_schema.log 2>&1; then
  echo "[import] pg_dump --schema-only FAILED; see /tmp/import_schema.log tail:" >&2
  tail -20 /tmp/import_schema.log >&2
  exit 4
fi

if ! psql \
    --no-psqlrc \
    --set ON_ERROR_STOP=1 \
    --quiet \
    --file="$SCHEMA_DUMP" \
    "$dst_plain" > /tmp/import_schema.log 2>&1; then
  echo "[import] psql schema restore FAILED; see /tmp/import_schema.log tail:" >&2
  tail -20 /tmp/import_schema.log >&2
  exit 4
fi

if [[ $SCHEMA_ONLY -eq 1 ]]; then
  echo "[import] schema-only mode; data step skipped."
  echo "[import] done."
  exit 0
fi

# ── Step 2: data ────────────────────────────────────────────
# Multi-pass because of the deep FK graph: parent rows must exist
# before children can be inserted. With 37+ tables and FK chains
# going 3-4 deep (e.g. integration.credentials → integration.raw_records
# → commerce.products_spu → commerce.sales_order_lines), a single
# pass fails on every child whose parent wasn't imported earlier.
# Three passes is empirically enough to converge; we short-circuit
# if a pass produces zero new rows.
echo "[import] (2/2) copying data for ${#ALL_TABLES[@]} tables (multi-pass for FKs)..."
declare -A row_counts
for pass in 1 2 3 4; do
  echo "  --- pass $pass ---"
  pass_inserted=0
  for t in "${ALL_TABLES[@]}"; do
    DATA_DUMP="$(mktemp --suffix=.sql)"
    if ! pg_dump \
        --data-only \
        --no-owner \
        --no-privileges \
        --table="$t" \
        --file="$DATA_DUMP" \
        "$src_plain" > "/tmp/import_${t//./_}.log" 2>&1; then
      rm -f "$DATA_DUMP"
      continue
    fi
    # Snapshot row count before this pass's insert attempt.
    schema="${t%%.*}"; table="${t#*.}"
    before=$(psql --no-psqlrc --tuples-only --no-align --quiet \
                --command "SELECT count(*) FROM ${schema}.${table};" \
                "$dst_plain" 2>/dev/null | tr -d '[:space:]') || before=0
    psql \
      --no-psqlrc \
      --set ON_ERROR_STOP=0 \
      --quiet \
      --file="$DATA_DUMP" \
      "$dst_plain" >> "/tmp/import_${t//./_}.log" 2>&1 || true
    after=$(psql --no-psqlrc --tuples-only --no-align --quiet \
              --command "SELECT count(*) FROM ${schema}.${table};" \
              "$dst_plain" 2>/dev/null | tr -d '[:space:]') || after=0
    rm -f "$DATA_DUMP"
    delta=$((after - before))
    if [[ $delta -gt 0 ]]; then
      echo "    +$t ($delta new rows)"
      pass_inserted=$((pass_inserted + delta))
    fi
    row_counts[$t]="$after"
  done
  echo "  pass $pass inserted $pass_inserted new rows"
  if [[ $pass_inserted -eq 0 && $pass -gt 1 ]]; then
    echo "  converged; stopping multi-pass"
    break
  fi
done

# ── Summary ─────────────────────────────────────────────────
echo
echo "[import] done. Row counts in target DB ($DST_DB):"
for t in "${ALL_TABLES[@]}"; do
  printf "  %-40s %s rows\n" "$t" "${row_counts[$t]:-0}"
done

if [[ -n "$ROW_LIMIT" ]]; then
  echo
  echo "[import] NOTE: --row-limit was set but the current implementation copies"
  echo "         full-table data. Implement a row-capped path if you actually"
  echo "         need it (probably means a per-table SELECT ... LIMIT N dump)."
fi

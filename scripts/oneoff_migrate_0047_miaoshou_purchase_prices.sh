#!/usr/bin/env bash
# Human-operated migration + first run for scheduled Miaoshou purchase prices.
#
#   ALLOW_PROD_DESTRUCTIVE=1 bash scripts/oneoff_migrate_0047_miaoshou_purchase_prices.sh --confirm
set -euo pipefail
SCRIPT_PATH=$(realpath "$0")
cd "$(dirname "$SCRIPT_PATH")/.."
TARGET="0047_miaoshou_purchase_price"
CONFIRMED=0
for arg in "$@"; do
    case "$arg" in
    --confirm) CONFIRMED=1 ;;
    -h | --help)
        sed -n '2,5p' "$SCRIPT_PATH"
        exit 0
        ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done
if [[ "$CONFIRMED" -ne 1 ]]; then
    echo "--confirm is required" >&2
    exit 2
fi
set -a
# shellcheck disable=SC1091
source .env
set +a
PYTHON=.venv/bin/python
ALEMBIC=.venv/bin/alembic
[[ -x "$PYTHON" && -x "$ALEMBIC" ]] || { echo ".venv incomplete" >&2; exit 1; }
[[ -f alembic/versions/0047_miaoshou_purchase_price_clean.py ]] || { echo "migration 0047 missing" >&2; exit 1; }

"$PYTHON" - <<'PY'
from tts_erp_v2.api.deps import require_destructive_script_guard
require_destructive_script_guard(
    script_name="oneoff_migrate_0047_miaoshou_purchase_prices",
    confirmation=True,
    dangerous=True,
    allow_env="ALLOW_PROD_DESTRUCTIVE",
)
PY
HEADS=$(timeout 30 "$ALEMBIC" heads)
[[ "$HEADS" == *"$TARGET"* ]] || { echo "Alembic head is not $TARGET" >&2; exit 1; }

"$PYTHON" - <<'PY'
from sqlalchemy import select
from tts_erp_v2.db.base import get_engine,get_session_factory
from tts_erp_v2.db.models.integration import Credentials
with get_session_factory(get_engine())() as session:
    rows=session.execute(select(Credentials.external_account_id).where(Credentials.provider=='miaoshou_web')).scalars().all()
if len(rows) != 1:
    raise SystemExit(
        "expected exactly one miaoshou_web credential; configure it first with "
        "scripts/configure_miaoshou_web_session.py"
    )
print(f"credential account={rows[0]}")
PY

WAS_ACTIVE=0
if timeout 10 systemctl --user is-active --quiet tts-erp-sync.service; then WAS_ACTIVE=1; fi
restore_worker() {
    if [[ "$WAS_ACTIVE" -eq 1 ]] && ! systemctl --user is-active --quiet tts-erp-sync.service; then
        timeout 60 systemctl --user start tts-erp-sync.service || true
    fi
}
trap restore_worker EXIT
if [[ "$WAS_ACTIVE" -eq 1 ]]; then timeout 300 systemctl --user stop tts-erp-sync.service; fi

timeout 300 "$ALEMBIC" upgrade "$TARGET"
"$PYTHON" - <<'PY'
from sqlalchemy import create_engine,text
import os
with create_engine(os.environ['TTS_ERP_DB_URL']).connect() as conn:
    revision=conn.execute(text('select version_num from alembic_version')).scalar_one()
    if revision!='0047_miaoshou_purchase_price': raise SystemExit(f'bad revision {revision}')
    tables={r[0] for r in conn.execute(text("select table_name from information_schema.tables where table_schema='miaoshou'"))}
    missing={'purchase_order_raw_records','purchase_prices'}-tables
    if missing: raise SystemExit(f'missing tables {sorted(missing)}')
print('migration 0047 verified')
PY

timeout 900 "$PYTHON" -m tts_erp_v2.sync_worker.main run miaoshou.purchase_price_clean
"$PYTHON" - <<'PY'
from sqlalchemy import create_engine,text
import os
with create_engine(os.environ['TTS_ERP_DB_URL']).connect() as conn:
    job=conn.execute(text("select status,rows_total,rows_inserted,rows_failed,extra from integration.sync_jobs where job_name='miaoshou.purchase_price_clean' order by id desc limit 1")).mappings().one()
    prices=conn.execute(text('select count(*) from miaoshou.purchase_prices')).scalar_one()
    unmatched=conn.execute(text("select count(*) from miaoshou.purchase_prices where resolution_status != 'matched_shop'" )).scalar_one()
print(dict(job))
print({'purchase_prices':prices,'unmatched_shops':unmatched})
if job['status']!='succeeded': raise SystemExit('purchase price job failed')
PY

if [[ "$WAS_ACTIVE" -eq 1 ]]; then
    timeout 60 systemctl --user start tts-erp-sync.service
    timeout 20 systemctl --user is-active --quiet tts-erp-sync.service
fi
trap - EXIT
restore_worker
echo "migration 0047 and initial purchase-price sync complete"

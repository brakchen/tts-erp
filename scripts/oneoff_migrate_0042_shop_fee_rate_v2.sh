#!/usr/bin/env bash
# Apply migration 0042 (fee-v2 kept-only snapshot contract), verify it, restart
# API + sync worker, and immediately rebuild all shop fee-rate snapshots.
#
# Production use (human-operated only):
#   ALLOW_PROD_DESTRUCTIVE=1 bash scripts/oneoff_migrate_0042_shop_fee_rate_v2.sh --confirm
#
# ``--confirm`` is mandatory. Restart and recomputation are intentionally not
# optional: 0042 invalidates ambiguous fee-v1 rows, and the new reader accepts
# only fee-v2. A coordinated restart plus immediate job run avoids both old-code
# column access and a 24-hour baseline-only window.
#
# Idempotent: Alembic upgrade at head is a no-op; the job UPSERTs by
# (shop_pk, calculated_on).
set -euo pipefail
SCRIPT_PATH=$(realpath "$0")
cd "$(dirname "$SCRIPT_PATH")/.."

CONFIRMED=0
for arg in "$@"; do
    case "$arg" in
    --confirm) CONFIRMED=1 ;;
    -h | --help)
        sed -n '2,16p' "$SCRIPT_PATH"
        exit 0
        ;;
    *)
        echo "❌ 未知参数: $arg" >&2
        exit 2
        ;;
    esac
done

if [[ "$CONFIRMED" -ne 1 ]]; then
    echo "❌ 必须显式传入 --confirm" >&2
    echo "用法: ALLOW_PROD_DESTRUCTIVE=1 bash $SCRIPT_PATH --confirm" >&2
    exit 2
fi

if [[ ! -f .env ]]; then
    echo "❌ .env 不存在（脚本依赖它取 TTS_ERP_DB_URL）" >&2
    exit 1
fi
set -a
# shellcheck disable=SC1091
source .env
set +a

PYTHON=".venv/bin/python"
ALEMBIC=".venv/bin/alembic"
if [[ ! -x "$PYTHON" || ! -x "$ALEMBIC" ]]; then
    echo "❌ .venv 不完整，请先恢复项目虚拟环境" >&2
    exit 1
fi
if [[ -z "${TTS_ERP_DB_URL:-}" ]]; then
    echo "❌ TTS_ERP_DB_URL 未设置" >&2
    exit 1
fi

MIGRATION_FILE="alembic/versions/0042_shop_fee_rate_v2.py"
TARGET="0042_shop_fee_rate_v2"
if [[ ! -f "$MIGRATION_FILE" ]]; then
    echo "❌ 当前代码不包含 migration 0042，请先部署 fix/shop-fee-rate-review" >&2
    exit 1
fi

read -r DB_NAME DB_HOST < <(
    "$PYTHON" - <<'PY'
import os
from sqlalchemy.engine import make_url
url = make_url(os.environ["TTS_ERP_DB_URL"])
print(url.database or "<unknown>", url.host or "<local>")
PY
)

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║ Migration 0042: 店铺平台费率 fee-v2                            ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo "数据库:      ${DB_NAME} @ ${DB_HOST}"
echo "目标版本:    ${TARGET}"
echo "restart:     required"
echo "run-job:     required"
echo

# 共享的 prod-shape 守卫。脚本自己不设 ALLOW_PROD_DESTRUCTIVE —— 必须由人工显式
# export，这样「脚本被误跑」和「人工有意放行」在日志上是可区分的。
"$PYTHON" - <<'PY'
from tts_erp_v2.api.deps import require_destructive_script_guard
require_destructive_script_guard(
    script_name="oneoff_migrate_0042_shop_fee_rate_v2",
    confirmation=True,
    dangerous=True,
    allow_env="ALLOW_PROD_DESTRUCTIVE",
)
print("✅ destructive guard passed")
PY

echo
echo "── Alembic 当前状态 ──"
timeout 30 "$ALEMBIC" current
HEADS=$(timeout 30 "$ALEMBIC" heads)
echo "── Alembic heads ──"
printf '%s\n' "$HEADS"
if [[ "$HEADS" != *"$TARGET"* ]]; then
    echo "❌ 当前代码的 Alembic head 不是 ${TARGET}，停止执行（避免误升级到别的版本）" >&2
    exit 1
fi

echo
echo "── 执行迁移（保留旧行但标记 legacy；默认版本切到 fee-v2）──"
timeout 300 "$ALEMBIC" upgrade "$TARGET"

echo
echo "── 验证 schema（只查 catalog，不读业务数据）──"
"$PYTHON" - <<'PY'
import os
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["TTS_ERP_DB_URL"])
with engine.connect() as conn:
    revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    if revision != "0042_shop_fee_rate_v2":
        raise SystemExit(f"❌ alembic revision 异常: {revision}")

    table_ok = conn.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='reporting' AND table_name='shop_fee_rate_estimates')"
        )
    ).scalar_one()
    if not table_ok:
        raise SystemExit("❌ reporting.shop_fee_rate_estimates 不存在")

    expected_cols = {
        "shop_pk", "calculated_on", "lookback_days", "fee_rate",
        "kept_order_count", "kept_line_gmv", "window_line_gmv",
        "kept_share", "total_fee", "currency", "calculation_version",
        "calculated_at", "created_at", "updated_at",
    }
    cols = {
        r[0]
        for r in conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='reporting' AND table_name='shop_fee_rate_estimates'"
            )
        ).fetchall()
    }
    missing = expected_cols - cols
    if missing:
        raise SystemExit(f"❌ 缺列: {sorted(missing)}")

    version_default = conn.execute(
        text(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_schema='reporting' "
            "AND table_name='shop_fee_rate_estimates' "
            "AND column_name='calculation_version'"
        )
    ).scalar_one()
    if "fee-v2" not in (version_default or ""):
        raise SystemExit(f"❌ calculation_version 默认值异常: {version_default}")

    constraints = {
        r[0]
        for r in conn.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'reporting.shop_fee_rate_estimates'::regclass"
            )
        ).fetchall()
    }
    for need in (
        "uq_shop_fee_rate_est_shop_day",
        "ck_shop_fee_rate_est_rate",
        "ck_shop_fee_rate_est_kept_share",
    ):
        if need not in constraints:
            raise SystemExit(f"❌ 缺约束: {need}")

    idx_ok = conn.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname='reporting' "
            "AND tablename='shop_fee_rate_estimates' "
            "AND indexname='ix_shop_fee_rate_est_shop_calc_at')"
        )
    ).scalar_one()
    if not idx_ok:
        raise SystemExit("❌ 缺索引 ix_shop_fee_rate_est_shop_calc_at")

print("✅ revision = 0042_shop_fee_rate_v2")
print("✅ reporting.shop_fee_rate_estimates 表 / 15 列 / 唯一+CHECK 约束 / 索引 齐备")
PY

echo
echo "── 重启 API 与 sync worker（0042 强制协调步骤）──"
timeout 30 systemctl --user restart tts-erp.service
timeout 30 systemctl --user restart tts-erp-sync.service
sleep 3
timeout 20 systemctl --user is-active --quiet tts-erp.service
timeout 20 systemctl --user is-active --quiet tts-erp-sync.service
PORT="${TTS_ERP_PORT:-9877}"
curl -fsS -m 10 "http://127.0.0.1:${PORT}/healthz" >/dev/null
echo "✅ 两个服务 active，healthz 正常"

echo
echo "── 立即重算 analytics.shop_fee_rate（写入 fee-v2）──"
timeout 900 "$PYTHON" -m tts_erp_v2.sync_worker.main run analytics.shop_fee_rate

echo
echo "── 当前快照（最新一日的每店费率；无行 = 该店回退基线）──"
"$PYTHON" - <<'PY'
import os
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["TTS_ERP_DB_URL"])
with engine.connect() as conn:
    rows = conn.execute(
        text(
            """
            SELECT s.shop_id,
                   e.shop_pk,
                   e.calculated_on,
                   e.fee_rate,
                   e.kept_order_count,
                   e.kept_share,
                   e.lookback_days,
                   e.currency,
                   e.calculation_version
            FROM reporting.shop_fee_rate_estimates e
            JOIN commerce.shops s ON s.id = e.shop_pk
            WHERE e.calculation_version = 'fee-v2'
              AND e.calculated_on = (
                  SELECT max(calculated_on)
                  FROM reporting.shop_fee_rate_estimates
                  WHERE calculation_version = 'fee-v2'
              )
            ORDER BY e.fee_rate DESC
            """
        )
    ).fetchall()
    total_shops = conn.execute(
        text("SELECT count(*) FROM commerce.shops WHERE platform='tiktok'")
    ).scalar_one()

if not rows:
    print("（空）—— 若刚跑过 job，说明窗口内没有任何已结算订单（或有已结算订单但无 FEE 分项），")
    print("       或窗口内没有带 FEE 分项的已结算交易。详情看 job 日志与")
    print("       integration.sync_jobs 里 job_name='analytics.shop_fee_rate' 那行的 extra。")
else:
    print(f"{'shop_id':>22}  {'费率':>9}  {'未退款单':>8}  {'占窗口GMV':>9}  窗口  币种  版本")
    for r in rows:
        print(
            f"{r[0]:>22}  {float(r[3]) * 100:8.2f}%  {r[4]:>7}  "
            f"{float(r[5]) * 100:7.1f}%  {r[6]:>4}d  {r[7]}  {r[8]}"
        )
    print()
    print(f"覆盖 {len(rows)} / {total_shops} 个 TikTok 店铺；其余走全局基线 0.308。")
PY
echo
echo "── 人工验收 ──"
PORT="${TTS_ERP_PORT:-9877}"
echo "1. 打开 http://127.0.0.1:${PORT}/v2/pages/spu-roi"
echo "2. 选一个刚才有实测费率的店铺 → 应出现「店铺实测」徽章的费率状态卡，"
echo "   并显示未退款订单数 / 占窗口GMV 比 / 重算日期"
echo "3. 在「临时覆写费率 %」填数字 → 徽章应变「页面覆写」；清空 → 回到实测/基线"
echo "4. 若某店显示「全局基线」+ 红色降级提示 → 该店在窗口内暂无已结算订单（或快照已过期），属预期行为"
echo
echo "回滚（如需）：${ALEMBIC} downgrade 0041_shop_fee_rate_kept_only"
echo
echo "✅ Migration 0042 完成"

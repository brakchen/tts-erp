#!/usr/bin/env bash
# Apply migration 0040 (reporting.shop_fee_rate_estimates — 店铺级平台抽成费率
# 日快照), verify it, optionally restart the API + sync worker, and optionally run
# the new analytics.shop_fee_rate job once so the page has data immediately.
#
# 前置：feature/shop-fee-rate 已合并到 master（脚本要求 migration 文件存在），
#      且 master 工作区是干净的可部署状态。
#
# 生产用法（仅人工执行）：
#   ALLOW_PROD_DESTRUCTIVE=1 bash scripts/oneoff_migrate_0040_shop_fee_rate.sh --confirm
#   ALLOW_PROD_DESTRUCTIVE=1 bash scripts/oneoff_migrate_0040_shop_fee_rate.sh --confirm --restart
#   ALLOW_PROD_DESTRUCTIVE=1 bash scripts/oneoff_migrate_0040_shop_fee_rate.sh --confirm --restart --run-job
#
# 参数：
#   --confirm    必填。没有它脚本什么都不做（对齐 alembic env.py 的守卫约定）。
#   --restart    迁移成功后重启 tts-erp.service（API）与 tts-erp-sync.service。
#   --run-job    立即跑一次 analytics.shop_fee_rate，最后打印每店费率。
#
# 为什么 --run-job 很重要：
#   新 job 以 APScheduler IntervalTrigger(seconds=86400) 注册，**首跑要等满
#   24 小时**（add_job 未设 next_run_time）。不加 --run-job 的话，spu-roi 会连续
#   24h 走全局基线 0.308 —— 不是错误，但你会以为功能没生效。
#
# 幂等：重复执行安全。alembic upgrade 到已应用的版本是 no-op（会打印
#       "already at head"）；job 按 (shop_pk, calculated_on) UPSERT。
set -euo pipefail
SCRIPT_PATH=$(realpath "$0")
cd "$(dirname "$SCRIPT_PATH")/.."

CONFIRMED=0
RESTART=0
RUN_JOB=0
for arg in "$@"; do
  case "$arg" in
    --confirm) CONFIRMED=1 ;;
    --restart) RESTART=1 ;;
    --run-job) RUN_JOB=1 ;;
    -h|--help)
      sed -n '2,26p' "$SCRIPT_PATH"
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
  echo "用法: ALLOW_PROD_DESTRUCTIVE=1 bash $SCRIPT_PATH --confirm [--restart] [--run-job]" >&2
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

MIGRATION_FILE="alembic/versions/0040_shop_fee_rate_estimates.py"
TARGET="0040_shop_fee_rate_estimates"
if [[ ! -f "$MIGRATION_FILE" ]]; then
  echo "❌ 当前代码不包含 migration 0040，请先把 feature/shop-fee-rate 合并到 master 并拉取" >&2
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
echo "║ Migration 0040: 店铺级平台抽成费率日快照                       ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo "数据库:      ${DB_NAME} @ ${DB_HOST}"
echo "目标版本:    ${TARGET}"
echo "restart:     ${RESTART}"
echo "run-job:     ${RUN_JOB}"
echo

# 共享的 prod-shape 守卫。脚本自己不设 ALLOW_PROD_DESTRUCTIVE —— 必须由人工显式
# export，这样「脚本被误跑」和「人工有意放行」在日志上是可区分的。
"$PYTHON" - <<'PY'
from tts_erp_v2.api.deps import require_destructive_script_guard
require_destructive_script_guard(
    script_name="oneoff_migrate_0040_shop_fee_rate",
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
echo "── 执行迁移（additive：只新建一张派生表）──"
timeout 300 "$ALEMBIC" upgrade "$TARGET"

echo
echo "── 验证 schema（只查 catalog，不读业务数据）──"
"$PYTHON" - <<'PY'
import os
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["TTS_ERP_DB_URL"])
with engine.connect() as conn:
    revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    if revision != "0040_shop_fee_rate_estimates":
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
        "eligible_order_count", "gross_sales_covered", "gross_sales_total",
        "coverage_ratio", "total_fee", "currency", "calculation_version",
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
        "ck_shop_fee_rate_est_coverage",
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

print("✅ revision = 0040_shop_fee_rate_estimates")
print("✅ reporting.shop_fee_rate_estimates 表 / 14 列 / 唯一+CHECK 约束 / 索引 齐备")
PY

if [[ "$RESTART" -eq 1 ]]; then
  echo
  echo "── 重启 API 与 sync worker ──"
  echo "   （API 重启加载 pages.py / spu_roi.py 改动；sync worker 重启注册新 job）"
  timeout 30 systemctl --user restart tts-erp.service
  timeout 30 systemctl --user restart tts-erp-sync.service
  sleep 3
  timeout 20 systemctl --user is-active --quiet tts-erp.service
  timeout 20 systemctl --user is-active --quiet tts-erp-sync.service
  PORT="${TTS_ERP_PORT:-9877}"
  curl -fsS -m 10 "http://127.0.0.1:${PORT}/healthz" >/dev/null
  echo "✅ 两个服务 active，healthz 正常"
else
  echo
  echo "ℹ️ 未重启服务。代码部署完成后执行："
  echo "   bash restart.sh"
  echo "   systemctl --user restart tts-erp-sync.service"
fi

if [[ "$RUN_JOB" -eq 1 ]]; then
  echo
  echo "── 立即跑一次 analytics.shop_fee_rate（否则要等满 24h 才首跑）──"
  timeout 900 "$PYTHON" -m tts_erp_v2.sync_worker.main run analytics.shop_fee_rate
else
  echo
  echo "ℹ️ 未跑 job。若不想等 24h，可执行："
  echo "   ${PYTHON} -m tts_erp_v2.sync_worker.main run analytics.shop_fee_rate"
fi

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
                   e.eligible_order_count,
                   e.coverage_ratio,
                   e.lookback_days,
                   e.currency
            FROM reporting.shop_fee_rate_estimates e
            JOIN commerce.shops s ON s.id = e.shop_pk
            WHERE e.calculated_on = (
                SELECT max(calculated_on) FROM reporting.shop_fee_rate_estimates
            )
            ORDER BY e.fee_rate DESC
            """
        )
    ).fetchall()
    total_shops = conn.execute(
        text("SELECT count(*) FROM commerce.shops WHERE platform='tiktok'")
    ).scalar_one()

if not rows:
    print("（空）—— 若刚跑过 job，说明所有店铺都没过门槛（样本 <50 单或覆盖率 <80%），")
    print("       或窗口内没有带 FEE 分项的已结算交易。详情看 job 日志与")
    print("       integration.sync_jobs 里 job_name='analytics.shop_fee_rate' 那行的 extra。")
else:
    print(f"{'shop_id':>22}  {'费率':>9}  {'样本单':>7}  {'覆盖率':>8}  窗口  币种")
    for r in rows:
        print(
            f"{r[0]:>22}  {float(r[3]) * 100:8.2f}%  {r[4]:>7}  "
            f"{float(r[5]) * 100:7.1f}%  {r[6]:>4}d  {r[7]}"
        )
    print()
    print(f"覆盖 {len(rows)} / {total_shops} 个 TikTok 店铺；其余走全局基线 0.308。")
PY
echo
echo "── 人工验收 ──"
PORT="${TTS_ERP_PORT:-9877}"
echo "1. 打开 http://127.0.0.1:${PORT}/v2/pages/spu-roi"
echo "2. 选一个刚才有实测费率的店铺 → 应出现「店铺实测」徽章的费率状态卡，"
echo "   并显示样本量 / 覆盖率 / 重算日期"
echo "3. 在「临时覆写费率 %」填数字 → 徽章应变「页面覆写」；清空 → 回到实测/基线"
echo "4. 若某店显示「全局基线」并带红色降级提示 → 该店样本或覆盖率未达标，属预期行为"
echo
echo "回滚（如需）：${ALEMBIC} downgrade 0039_tiktok_app_credentials"
echo
echo "✅ Migration 0040 完成"

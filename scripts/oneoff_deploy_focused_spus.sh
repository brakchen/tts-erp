#!/usr/bin/env bash
# Apply migration 0043, verify the focused-SPU schema, restart the API service,
# and verify that the new page/API routes are registered.
#
# Read-only preflight:
#   bash scripts/oneoff_deploy_focused_spus.sh --check
#
# Production deployment (human-operated only):
#   ALLOW_PROD_DESTRUCTIVE=1 \
#     bash scripts/oneoff_deploy_focused_spus.sh --confirm
#
# The script intentionally does not run git pull and does not restart the sync
# worker: deploy current origin/master before running it; this feature changes
# only the API process. Re-running at 0043 is idempotent.
set -euo pipefail

SCRIPT_PATH=$(realpath "$0")
cd "$(dirname "$SCRIPT_PATH")/.."

MODE=""
for arg in "$@"; do
  case "$arg" in
    --check) MODE="check" ;;
    --confirm) MODE="apply" ;;
    -h | --help)
      sed -n '2,15p' "$SCRIPT_PATH"
      exit 0
      ;;
    *)
      echo "❌ 未知参数: $arg" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$MODE" ]]; then
  echo "❌ 必须传 --check 或 --confirm" >&2
  echo "用法:" >&2
  echo "  bash $SCRIPT_PATH --check" >&2
  echo "  ALLOW_PROD_DESTRUCTIVE=1 bash $SCRIPT_PATH --confirm" >&2
  exit 2
fi

if [[ ! -f .env ]]; then
  echo "❌ .env 不存在（脚本依赖它读取 TTS_ERP_DB_URL）" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1091
source .env
set +a

PYTHON=".venv/bin/python"
ALEMBIC=".venv/bin/alembic"
TARGET="0043_focused_spus"
PREVIOUS="0042_shop_fee_rate_v2"
MIGRATION_FILE="alembic/versions/0043_focused_spus.py"

if [[ ! -x "$PYTHON" || ! -x "$ALEMBIC" ]]; then
  echo "❌ .venv 不完整，请先恢复项目虚拟环境" >&2
  exit 1
fi
if [[ -z "${TTS_ERP_DB_URL:-}" ]]; then
  echo "❌ TTS_ERP_DB_URL 未设置" >&2
  exit 1
fi
if [[ ! -f "$MIGRATION_FILE" ]]; then
  echo "❌ 当前代码不包含 migration 0043，请先部署最新 origin/master" >&2
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

CURRENT=$(
  "$PYTHON" - <<'PY'
import os
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["TTS_ERP_DB_URL"])
with engine.connect() as conn:
    print(conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one())
PY
)
HEADS=$(timeout 30 "$ALEMBIC" heads)

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║ Migration 0043: 重点关注 SPU                                  ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo "数据库:      ${DB_NAME} @ ${DB_HOST}"
echo "当前版本:    ${CURRENT}"
echo "代码 head:   ${TARGET}"
echo "执行模式:    ${MODE}"
echo

if [[ "$HEADS" != *"$TARGET"* ]]; then
  echo "❌ 当前代码的 Alembic head 不是 ${TARGET}，停止执行" >&2
  printf '%s\n' "$HEADS" >&2
  exit 1
fi
if [[ "$CURRENT" != "$PREVIOUS" && "$CURRENT" != "$TARGET" ]]; then
  echo "❌ 数据库版本必须是 ${PREVIOUS} 或 ${TARGET}，当前为 ${CURRENT}" >&2
  echo "   为避免一次跨过未知 migration，脚本已停止。" >&2
  exit 1
fi

if [[ "$MODE" == "check" ]]; then
  if [[ "$CURRENT" == "$TARGET" ]]; then
    echo "✅ 数据库已经在 ${TARGET}；可执行 --confirm 做 schema/服务复验。"
  else
    echo "✅ preflight 通过：可从 ${PREVIOUS} 安全升级到 ${TARGET}。"
  fi
  echo "下一步："
  echo "  git pull --ff-only origin master"
  echo "  ALLOW_PROD_DESTRUCTIVE=1 bash $SCRIPT_PATH --confirm"
  exit 0
fi

# Shared production-shaped DB guard. The script never sets the override itself;
# the human operator must provide it explicitly in the command environment.
"$PYTHON" - <<'PY'
from tts_erp_v2.api.deps import require_destructive_script_guard

require_destructive_script_guard(
    script_name="oneoff_deploy_focused_spus",
    confirmation=True,
    dangerous=True,
    allow_env="ALLOW_PROD_DESTRUCTIVE",
)
print("✅ destructive guard passed")
PY

echo
echo "── 执行 migration 0043 ──"
timeout 300 "$ALEMBIC" upgrade "$TARGET"

echo
echo "── 验证 schema（只查系统 catalog，不读取业务数据）──"
"$PYTHON" - <<'PY'
import os
from sqlalchemy import create_engine, text

TARGET = "0043_focused_spus"
engine = create_engine(os.environ["TTS_ERP_DB_URL"])
with engine.connect() as conn:
    revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    if revision != TARGET:
        raise SystemExit(f"❌ alembic revision 异常: {revision}")

    expected_columns = {
        "shop_pk",
        "spu_id",
        "active",
        "added_by",
        "removed_by",
        "removed_at",
        "created_at",
        "updated_at",
    }
    columns = {
        row[0]
        for row in conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='reporting' AND table_name='focused_spus'"
            )
        )
    }
    missing_columns = expected_columns - columns
    if missing_columns:
        raise SystemExit(f"❌ focused_spus 缺列: {sorted(missing_columns)}")

    constraints = {
        row[0]
        for row in conn.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid='reporting.focused_spus'::regclass"
            )
        )
    }
    expected_constraints = {
        "pk_focused_spus",
        "fk_focused_spus_shop",
        "fk_focused_spus_product",
        "ck_focused_spus_spu_id_length",
    }
    missing_constraints = expected_constraints - constraints
    if missing_constraints:
        raise SystemExit(f"❌ focused_spus 缺约束: {sorted(missing_constraints)}")

    indexes = {
        row[0]
        for row in conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname='reporting' AND tablename='focused_spus'"
            )
        )
    }
    expected_indexes = {
        "ix_focused_spus_active_membership",
        "ix_focused_spus_active_updated",
    }
    missing_indexes = expected_indexes - indexes
    if missing_indexes:
        raise SystemExit(f"❌ focused_spus 缺索引: {sorted(missing_indexes)}")

    trigger_ok = conn.execute(
        text(
            "SELECT EXISTS ("
            "SELECT 1 FROM pg_trigger "
            "WHERE tgrelid='reporting.focused_spus'::regclass "
            "AND tgname='trg_reporting_focused_spus_touch' AND NOT tgisinternal)"
        )
    ).scalar_one()
    if not trigger_ok:
        raise SystemExit("❌ 缺 trigger trg_reporting_focused_spus_touch")

print("✅ revision = 0043_focused_spus")
print("✅ focused_spus 表 / 8 列 / 复合主键+双外键+CHECK / 2 个索引 / touch trigger 齐备")
PY

echo
echo "── 重启 API 服务 ──"
timeout 30 systemctl --user restart tts-erp.service
sleep 3
timeout 20 systemctl --user is-active --quiet tts-erp.service
PORT="${TTS_ERP_PORT:-9877}"
curl -fsS -m 10 "http://127.0.0.1:${PORT}/healthz" >/dev/null

echo "── 验证路由注册与静态资产 ──"
curl -fsS -m 15 "http://127.0.0.1:${PORT}/openapi.json" |
  "$PYTHON" -c '
import json, sys
paths = json.load(sys.stdin).get("paths", {})
required = {
    "/v2/reporting/focused-spus/{shop_pk}",
    "/v2/pages/focused-spus",
    "/v2/analytics/spu-roi",
}
missing = required - paths.keys()
if missing:
    raise SystemExit(f"❌ 缺路由: {sorted(missing)}")
print("✅ focused management / page / analytics 路由已注册")
'
curl -fsS -m 10 \
  "http://127.0.0.1:${PORT}/static/js/spu-profitability-page.js" >/dev/null
curl -fsS -m 10 \
  "http://127.0.0.1:${PORT}/static/js/focused-spus.js" >/dev/null
curl -fsS -m 10 \
  "http://127.0.0.1:${PORT}/static/css/focused-spus.css" >/dev/null

echo "✅ API active、healthz 正常、路由和静态资产齐备"
echo
echo "── 人工验收 ──"
echo "1. 打开 http://127.0.0.1:${PORT}/v2/pages/focused-spus"
echo "2. 登录 readwrite/admin 账号，选择店铺并添加 1 个 SPU"
echo "3. 刷新页面，确认关注关系保留；再移除并确认页面更新"
echo "4. 用 readonly 账号确认可以查看，但“编辑关注 SPU”不可用"
echo
echo "⚠️ 回滚提示：downgrade 到 ${PREVIOUS} 会 DROP reporting.focused_spus，"
echo "   永久删除关注数据；不要由 agent 或自动脚本执行，必须人工另行确认。"
echo
echo "✅ 重点关注 SPU 上线完成"

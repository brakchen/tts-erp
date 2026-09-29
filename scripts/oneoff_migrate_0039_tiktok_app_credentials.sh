#!/usr/bin/env bash
# Apply migration 0039 (TikTok App credentials keyed by service_id), verify it,
# and optionally restart the API + sync worker.
#
# Production usage (human-operated only):
#   ALLOW_PROD_DESTRUCTIVE=1 bash scripts/oneoff_migrate_0039_tiktok_app_credentials.sh --confirm
#
# Add --restart to restart both services after a successful migration:
#   ALLOW_PROD_DESTRUCTIVE=1 bash scripts/oneoff_migrate_0039_tiktok_app_credentials.sh --confirm --restart
set -euo pipefail
SCRIPT_PATH=$(realpath "$0")
cd "$(dirname "$SCRIPT_PATH")/.."

CONFIRMED=0
RESTART=0
for arg in "$@"; do
  case "$arg" in
    --confirm) CONFIRMED=1 ;;
    --restart) RESTART=1 ;;
    -h|--help)
      sed -n '2,10p' "$SCRIPT_PATH"
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
  echo "用法: ALLOW_PROD_DESTRUCTIVE=1 bash $SCRIPT_PATH --confirm [--restart]" >&2
  exit 2
fi

if [[ ! -f .env ]]; then
  echo "❌ .env 不存在" >&2
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
if [[ ! -f alembic/versions/0039_tiktok_app_credentials.py ]]; then
  echo "❌ 当前代码不包含 migration 0039，请先合并/拉取功能分支" >&2
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

echo "╔══════════════════════════════════════════════════════════╗"
echo "║ Migration 0039: TikTok App credentials by service_id   ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo "数据库: ${DB_NAME} @ ${DB_HOST}"
echo "目标版本: 0039_tiktok_app_credentials"
echo

# Shared production-shaped DB guard. This does not set the override itself:
# the human operator must explicitly export ALLOW_PROD_DESTRUCTIVE=1.
"$PYTHON" - <<'PY'
from tts_erp_v2.api.deps import require_destructive_script_guard
require_destructive_script_guard(
    script_name="oneoff_migrate_0039_tiktok_app_credentials",
    confirmation=True,
    dangerous=True,
    allow_env="ALLOW_PROD_DESTRUCTIVE",
)
print("✅ destructive guard passed")
PY

echo "── Alembic 当前状态 ──"
timeout 30 "$ALEMBIC" current
echo "── Alembic heads ──"
HEADS=$(timeout 30 "$ALEMBIC" heads)
printf '%s\n' "$HEADS"
if [[ "$HEADS" != *"0039_tiktok_app_credentials"* ]]; then
  echo "❌ 当前代码的 Alembic head 不是 0039，停止执行" >&2
  exit 1
fi

echo
echo "── 执行迁移 ──"
timeout 300 "$ALEMBIC" upgrade 0039_tiktok_app_credentials

echo
echo "── 验证 schema（不读取任何密文内容）──"
"$PYTHON" - <<'PY'
import os
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["TTS_ERP_DB_URL"])
with engine.connect() as conn:
    revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    table_exists = conn.execute(text("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'integration'
              AND table_name = 'tiktok_app_credentials'
        )
    """)).scalar_one()
    service_col_exists = conn.execute(text("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'integration'
              AND table_name = 'credentials'
              AND column_name = 'service_id'
        )
    """)).scalar_one()
    secret_col_exists = conn.execute(text("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'integration'
              AND table_name = 'tiktok_app_credentials'
              AND column_name = 'app_secret_ciphertext'
        )
    """)).scalar_one()

if revision != "0039_tiktok_app_credentials":
    raise SystemExit(f"❌ alembic revision 异常: {revision}")
if not (table_exists and service_col_exists and secret_col_exists):
    raise SystemExit("❌ 0039 schema 验证失败")
print("✅ revision=0039_tiktok_app_credentials")
print("✅ integration.tiktok_app_credentials 已存在")
print("✅ integration.credentials.service_id 已存在")
print("✅ app_secret_ciphertext 已存在")
PY

if [[ "$RESTART" -eq 1 ]]; then
  echo
  echo "── 重启 API 与 sync worker ──"
  timeout 30 systemctl --user restart tts-erp.service
  timeout 30 systemctl --user restart tts-erp-sync.service
  sleep 2
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

echo
echo "✅ Migration 0039 完成"

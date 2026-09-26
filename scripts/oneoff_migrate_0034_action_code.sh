#!/usr/bin/env bash
# 一键执行 0034 migration（plugin.tracking_events 新增 action_code 列）并重启服务。
#
# 用法：
#   bash scripts/oneoff_migrate_0034_action_code.sh          # 交互确认后执行
#   bash scripts/oneoff_migrate_0034_action_code.sh --yes    # 跳过确认直接执行
set -euo pipefail
cd "$(dirname "$0")/.."

AUTO_YES=0
if [[ "${1:-}" == "--yes" || "${1:-}" == "-y" ]]; then
  AUTO_YES=1
fi

# ── 1. 加载 .env ────────────────────────────────────────────────────
if [ ! -f .env ]; then
  echo "❌ .env 不存在，请先创建" >&2
  exit 1
fi
set -a
source .env
set +a

DB_URL="${TTS_ERP_DB_URL:-}"
if [ -z "$DB_URL" ]; then
  echo "❌ TTS_ERP_DB_URL 未设置" >&2
  exit 1
fi

# 从 URL 中提取 dbname 用于展示（隐藏密码）
DB_NAME=$(echo "$DB_URL" | sed -E 's|.*/([^?]+).*|\1|')
DB_HOST=$(echo "$DB_URL" | sed -E 's|.*@([^:/]+).*|\1|')

echo "╔══════════════════════════════════════════════════════╗"
echo "║  Migration 0034: plugin.tracking_events.action_code  ║"
echo "╚══════════════════════════════════════════════════════╝"
echo
echo "  数据库: $DB_NAME @ $DB_HOST"
echo

# ── 2. 检查是否为生产库 ─────────────────────────────────────────────
if [[ "$DB_NAME" == "tts_erp" || "$DB_NAME" == "tts_erp_prod" ]]; then
  echo "⚠️  当前连接的是生产库 ($DB_NAME)"
  if [ "$AUTO_YES" -ne 1 ]; then
    read -r -p "确认在生产库执行迁移？[y/N] " ans
    if [[ ! "$ans" =~ ^[yY]$ ]]; then
      echo "已取消"
      exit 0
    fi
  fi
fi

# ── 3. 交互确认 ─────────────────────────────────────────────────────
if [ "$AUTO_YES" -ne 1 ]; then
  echo "即将执行以下操作："
  echo "  1. alembic upgrade head"
  echo "  2. 验证 action_code 列已存在"
  echo "  3. 重启 tts-erp API"
  echo "  4. 重启 tts-erp-sync worker"
  echo
  read -r -p "继续？[y/N] " ans
  if [[ ! "$ans" =~ ^[yY]$ ]]; then
    echo "已取消"
    exit 0
  fi
fi

ALEMBIC=".venv/bin/alembic"
if [ ! -x "$ALEMBIC" ]; then
  ALEMBIC="/home/schan/tts-erp/.venv/bin/alembic"
fi
if [ ! -x "$ALEMBIC" ]; then
  echo "❌ alembic 未找到" >&2
  exit 1
fi

# ── 4. 当前迁移状态 ────────────────────────────────────────────────
echo
echo "── 当前迁移状态 ──"
$ALEMBIC current
echo

# ── 5. 执行迁移 ─────────────────────────────────────────────────────
echo "── 执行 alembic upgrade head ──"
$ALEMBIC upgrade head
echo "✅ 迁移完成"
echo

# ── 6. 验证列已存在 ────────────────────────────────────────────────
echo "── 验证 action_code 列 ──"
RESULT=$(psql "$DB_URL" -t -A -c "
  SELECT column_name || ':' || data_type
  FROM information_schema.columns
  WHERE table_schema = 'plugin'
    AND table_name = 'tracking_events'
    AND column_name = 'action_code';
" 2>/dev/null || echo "QUERY_FAILED")

if [[ "$RESULT" == *"action_code:integer"* ]]; then
  echo "✅ plugin.tracking_events.action_code (integer) 已存在"
elif [[ "$RESULT" == "QUERY_FAILED" ]]; then
  echo "⚠️  psql 查询失败，请手动验证："
  echo "  psql \"\$TTS_ERP_DB_URL\" -c \"\\d plugin.tracking_events\""
else
  echo "❌ 验证失败，未检测到 action_code 列。返回: $RESULT" >&2
  exit 1
fi
echo

# ── 7. 重启 API ─────────────────────────────────────────────────────
echo "── 重启 API ──"
systemctl --user restart tts-erp.service
sleep 2
echo "✅ tts-erp.service 已重启"
systemctl --user --no-pager status tts-erp.service | head -3
echo

# ── 8. 重启 sync worker ────────────────────────────────────────────
echo "── 重启 sync worker ──"
systemctl --user restart tts-erp-sync.service
sleep 2
echo "✅ tts-erp-sync.service 已重启"
systemctl --user --no-pager status tts-erp-sync.service | head -3
echo

# ── 9. 健康检查 ─────────────────────────────────────────────────────
PORT="${TTS_ERP_PORT:-9877}"
echo "── 健康检查 ──"
if curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
  echo "✅ /healthz OK (port $PORT)"
else
  echo "⚠️  /healthz 无响应 (port $PORT)，请手动检查"
fi

echo
echo "╔══════════════════════════════════════════════════════╗"
echo "║  全部完成！                                          ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  部署后观察：                                        ║"
echo "║  • tracking_events.action_code 是否开始写入          ║"
echo "║  • /v2/order-sync/reconcile 是否返回 isTerminal      ║"
echo "║  • plugin_logs 中 dump 记录是否健康                  ║"
echo "╚══════════════════════════════════════════════════════╝"

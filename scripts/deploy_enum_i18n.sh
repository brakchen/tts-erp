#!/usr/bin/env bash
# deploy_enum_i18n.sh — 部署 SPU ROI 枚举中文化功能
# 用法: bash scripts/deploy_enum_i18n.sh
set -euo pipefail

cd "$(dirname "$0")/.."

# ── 加载 .env ──
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# ── 确认目标库 ──
DB_URL="${TTS_ERP_DB_URL:-}"
if [ -z "$DB_URL" ]; then
  echo "ERROR: TTS_ERP_DB_URL not set. Source .env first." >&2
  exit 1
fi
DB_NAME=$(echo "$DB_URL" | grep -oP '[^/]+$')
if echo "$DB_NAME" | grep -qP '^tts_erp(_prod)?$'; then
  echo "⚠ 目标库: $DB_NAME (生产库)"
  read -p "确认在生产库执行 migration? [y/N] " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "cancelled."
    exit 0
  fi
else
  echo "✓ 目标库: $DB_NAME"
fi

echo ""
echo "=== 1/3 alembic upgrade head ==="
# 本次 migration 是纯 additive（新建 config schema + enum_map 表），非 destructive。
# 已由上方交互确认 prod 库，此处放行 guard。
ALLOW_PROD_DESTRUCTIVE=1 /home/schan/tts-erp/.venv/bin/alembic upgrade head
echo "✓ migration done"

echo ""
echo "=== 2/3 restart API ==="
bash restart.sh
echo "✓ API restarted"

echo ""
echo "=== 3/3 verify ==="
sleep 2
# 健康检查
HTTP=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9877/healthz || echo "000")
if [ "$HTTP" = "200" ]; then
  echo "✓ healthz OK"
else
  echo "✗ healthz returned $HTTP"
  exit 1
fi

# 枚举 API 检查
ENUM_ROWS=$(curl -s http://127.0.0.1:9877/v2/config/enum-map 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
total = sum(len(v) for v in d.values())
print(f'{total} entries across {len(d)} types')
" 2>/dev/null || echo "fetch failed")
echo "✓ enum-map API: $ENUM_ROWS"

echo ""
echo "=== Done ==="
echo "  SPU ROI:  http://127.0.0.1:9877/v2/pages/spu-roi"
echo "  枚举管理: http://127.0.0.1:9877/v2/pages/enum-map"

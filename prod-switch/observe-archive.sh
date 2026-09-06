#!/usr/bin/env bash
# observe-archive.sh — 4 周观察期结束后的归档操作
# 用法：bash /home/schan/tts-erp/prod-switch/observe-archive.sh
# 前置：观察期内无回滚、无 sync_issues 异常增长
#
# ⚠️ 2026-09-05 状态：本次 oauth_receiver 库清理已按本脚本设计的全部动作手动执行
# （dump 备份 → 拆 systemd unit → 清理 .env OAUTH_* → 删 schema_oauth.sql →
#  regen_schema.py 单库化 → DROP DATABASE → 更新文档）。脚本本身保留作
# 未来类似收尾的 SOP 模板；oauth-receiver.service 已不存在，脚本内停服务 /
# disable 段运行时会报 unit not found 但被 || true 包裹不影响退出。
set -euo pipefail

REPO=/home/schan/tts-erp
cd "$REPO"

YEL='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

echo "=== Observation Period Pre-Checks ==="
echo "This script assumes 4 weeks have passed without rollback."

# 1. Check no rollback in journalctl
echo "→ checking for rollback markers in journalctl ..."
n=$(journalctl --user -u tts-erp.service --since "4 weeks ago" |
  grep -c "ROLLBACK\|reverted" || true)
if [ "$n" -eq 0 ]; then
  echo "  no rollback markers found"
else
  echo -e "${RED}  ${n} rollback markers — DO NOT archive yet${NC}"
  exit 1
fi

# 2. Check sync_issues not exploding
echo "→ checking sync_issues growth ..."
n=$(docker exec postgres psql -U postgres -d tts_erp -tAc \
  "SELECT count(*) FROM integration.sync_issues WHERE detected_at > NOW() - INTERVAL '7 days'")
if [ "$n" -lt 100 ]; then
  echo "  recent issues: $n (under threshold)"
else
  echo -e "${YEL}  ${n} recent issues — review before archive${NC}"
  read -rp "Continue anyway? type 'YES' to proceed: " ans
  [ "$ans" = "YES" ] || exit 1
fi

echo
echo "=== Archival Steps ==="
echo
echo "This script will:"
echo "  1. Stop oauth-receiver (:9876) — no longer needed after 4w"
echo "  2. Rename schema public → _deprecated_public_<YYYYMMDD>"
echo "  3. Drop the renamed schema (after 1 more week grace)"
echo "  4. Remove legacy cron jobs from crontab"
echo
read -rp "Ready to archive? type 'ARCHIVE' to proceed: " ans
[ "$ans" = "ARCHIVE" ] || {
  echo "aborted"
  exit 1
}

echo "→ stopping oauth-receiver"
systemctl --user stop oauth-receiver.service || true
systemctl --user disable oauth-receiver.service || true

echo "→ renaming public → _deprecated_public_$(date +%Y%m%d)"
docker exec postgres psql -U postgres -d tts_erp -c \
  "ALTER SCHEMA public RENAME TO _deprecated_public_$(date +%Y%m%d)"

echo "→ dropping legacy cron jobs from crontab"
(crontab -l 2>/dev/null | grep -v 'sync_cron\|oauth-receiver' || true) | crontab -

echo
echo "=== NEXT (manual, after 1 more week grace): ==="
echo "  docker exec postgres psql -U postgres -d tts_erp -c \\"
echo "    \"DROP SCHEMA _deprecated_public_$(date +%Y%m%d) CASCADE\""
echo
echo "==== ARCHIVAL COMPLETE ===="
